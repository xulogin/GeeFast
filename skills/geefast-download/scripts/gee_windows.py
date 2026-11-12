"""把 GEE 影像的一批小窗口取成本机 numpy 数组（有缓存，可中断重跑）。

适合「几百到几千个点，每个点取一块几十公里见方的图来做分析」的任务，
不写 GeoTIFF 文件，直接得到数组。

做法：
- 每个窗口切成 tile×tile 的小块，所有窗口的小块共用一个线程池并发取。
- 用 computePixels 的 GEO_TIFF 格式（无损压缩），在内存里解码。
  同一块图实测比 NPY 少传约 1/3，像元逐个相同。
- 结果按「名字」存成 .npy，再次调用直接读缓存。

环境变量：
  EE_PROJECT   必填，GEE 项目号
  EE_KEY       可选，服务账号密钥文件路径；不设就用本机 earthengine 登录
  GEEFAST_CACHE 可选，缓存目录，默认 ./gee_cache

当库用：
    import gee_windows as gw, ee
    gw.init()
    img = ee.ImageCollection('COPERNICUS/S2_HARMONIZED').filterDate('2024-01-01','2024-12-31') \
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 5)).select('B8').median().toUint16()
    a = gw.window(img, lon=49.5, lat=21.5, km=20, scale=10, name='p49.5_21.5_2024')
    # 多个窗口：gw.windows([(img1, lon1, lat1, name1), ...], km=20, scale=10, parallel=12)

命令行测速（不依赖任何影像，只测链路能传多快）：
    python gee_windows.py --speedtest
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

logging.getLogger("tifffile").setLevel(logging.CRITICAL)   # GEE 的 nodata 标签会让 tifffile 报无害的警告

CACHE = os.environ.get("GEEFAST_CACHE", os.path.join(os.getcwd(), "gee_cache"))
WORKERS = int(os.environ.get("GEEFAST_WORKERS", "96"))
_pool = ThreadPoolExecutor(WORKERS)        # 全进程共用，限制同时在途的取块请求数
_ready = False


def init() -> None:
    """连接 GEE 高容量端点。密钥文件只把路径交给客户端库，不打印内容。"""
    global _ready
    if _ready:
        return
    import ee
    project = os.environ.get("EE_PROJECT")
    if not project:
        raise SystemExit("请先设置环境变量 EE_PROJECT")
    kw = dict(project=project, opt_url="https://earthengine-highvolume.googleapis.com")
    key = os.environ.get("EE_KEY")
    if key:
        key = os.path.expanduser(key)
        email = json.load(open(key))["client_email"]
        kw["credentials"] = ee.ServiceAccountCredentials(email, key)
    ee.Initialize(**kw)
    _ready = True


def utm(lon: float, lat: float) -> str:
    """点所在的 UTM 带，米制投影，窗口不变形。"""
    return f"EPSG:{326 if lat >= 0 else 327}{int((lon + 180) // 6) + 1:02d}"


def fetch(img, crs: str, x0: float, y0: float, width: int, height: int, scale: float,
          tile: int = 500, tries: int = 5) -> np.ndarray:
    """取一块图。x0,y0 是左上角的投影坐标。单波段返回 (高, 宽)，多波段返回 (高, 宽, 波段)。"""
    import ee
    import tifffile

    def one(ij):
        i, j = ij
        h, w = min(tile, height - i), min(tile, width - j)
        req = {"expression": img, "fileFormat": "GEO_TIFF",
               "grid": {"dimensions": {"width": w, "height": h}, "crsCode": crs,
                        "affineTransform": {"scaleX": scale, "shearX": 0, "translateX": x0 + j * scale,
                                            "shearY": 0, "scaleY": -scale, "translateY": y0 - i * scale}}}
        for k in range(tries):
            try:
                return i, j, tifffile.imread(io.BytesIO(ee.data.computePixels(req)))
            except Exception:
                if k == tries - 1:
                    raise
                time.sleep(2 * (k + 1))        # 遇到限流或偶发错误就等一等再试

    out = None
    jobs = [(i, j) for i in range(0, height, tile) for j in range(0, width, tile)]
    for i, j, a in _pool.map(one, jobs):
        if out is None:
            out = np.zeros((height, width) + a.shape[2:], a.dtype)
        out[i:i + a.shape[0], j:j + a.shape[1]] = a
    return out


def window(img, lon: float, lat: float, km: float, scale: float, name: str | None = None,
           tile: int = 500) -> np.ndarray:
    """以 (lon, lat) 为中心取 km 见方的窗口。给了 name 就走缓存。"""
    f = os.path.join(CACHE, f"{name}.npy") if name else None
    if f and os.path.exists(f):
        return np.load(f)
    import ee
    init()
    crs = utm(lon, lat)
    cx, cy = ee.Geometry.Point([lon, lat]).transform(crs, 1).coordinates().getInfo()
    n = int(round(km * 1000 / scale))
    a = fetch(img, crs, cx - km * 500, cy + km * 500, n, n, scale, tile)
    if f:
        os.makedirs(CACHE, exist_ok=True)
        tmp = f + ".part.npy"
        np.save(tmp, a)
        os.replace(tmp, f)                     # 写完再改名，中断不会留下半个文件
    return a


def windows(items, km: float, scale: float, parallel: int = 12, tile: int = 500):
    """items 是 (img, lon, lat, name) 的列表。按顺序逐个产出 (name, 数组或异常)。"""
    def one(it):
        img, lon, lat, name = it
        try:
            return name, window(img, lon, lat, km, scale, name, tile)
        except Exception as e:                 # 单个点失败不拖垮整批
            return name, e
    with ThreadPoolExecutor(parallel) as ex:
        yield from ex.map(one, items)


def speedtest(n: int = 8, px: int = 2000) -> None:
    """用不需要计算的随机图测链路：结果就是这台机器到 GEE 的传输上限。"""
    import ee
    init()
    rnd = ee.Image.random(1).multiply(65535).toUint16()
    t = time.time()
    fetch(rnd, "EPSG:32639", 500000, 2400000, 10, 10, 10)
    print(f"往返延迟 {time.time() - t:.2f} s")
    t = time.time()
    with ThreadPoolExecutor(n) as ex:
        r = list(ex.map(lambda k: fetch(rnd.add(k).toUint16(), "EPSG:32639", 500000, 2400000, px, px, 10), range(n)))
    dt = time.time() - t
    mpx = n * px * px / 1e6
    print(f"{n} 幅 {px}×{px} 随机图：{dt:.1f} s，{mpx / dt:.2f} 百万像元/秒，约 {sum(a.nbytes for a in r) / dt / 1e6:.1f} MB/s（未压缩计）")
    print("真实影像若明显慢于这个数，慢在服务器计算；接近这个数，慢在网络。")


if __name__ == "__main__":
    if "--speedtest" in sys.argv:
        speedtest()
    else:
        print(__doc__)
