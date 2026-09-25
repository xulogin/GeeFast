"""原生 GEE computePixels 高速下载器。

它不依赖 geedim：直接调用 Earth Engine Python API 的 REST computePixels，
自己负责切块、并发、退避和按 tile 写入 GeoTIFF。

限制：仍受 GEE 交互式请求配额约束；默认 tile 2048、并发 20，单 tile
的未压缩字节数控制在官方 48 MB 限制内。
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import math
import os
import random
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="原生 GEE REST computePixels 下载器")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", help="单幅 EE Image 资产 ID；自动使用 getPixels")
    src.add_argument("--collection", help="EE ImageCollection ID")
    p.add_argument("--year", type=int)
    p.add_argument("--month", type=int)
    p.add_argument("--start-date", help="集合起始日期 YYYY-MM-DD，优先于 year/month")
    p.add_argument("--end-date", help="集合结束日期 YYYY-MM-DD，Earth Engine 为右开区间")
    p.add_argument("--reducer", choices=("median", "mosaic", "first"), default="median")
    p.add_argument("--bbox", required=True, help="xmin,ymin,xmax,ymax，经纬度")
    p.add_argument("--bands", required=True, help="逗号分隔波段")
    p.add_argument("--scale", type=float, required=True, help="米/像元")
    p.add_argument("--tile", type=int, default=2048, help="tile 边长，默认 2048")
    p.add_argument("--auto-tile", action="store_true",
                   help="按 45 MiB 安全预算自动选择 tile；考虑每波段 mask 开销")
    p.add_argument("--workers", type=int, default=20)
    p.add_argument("--endpoint", choices=("standard", "high-volume"), default="standard")
    p.add_argument("--dtype", choices=("auto", "float32", "int16", "uint16"), default="auto")
    p.add_argument("--auto-grid", action="store_true",
                   help="自动读取首波段原生投影并裁剪研究区，失败时回退到经纬度网格")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--crs-code", default="EPSG:4326")
    p.add_argument("--affine", help="六个仿射参数，适合 MODIS native grid")
    p.add_argument("--grid-width", type=int)
    p.add_argument("--grid-height", type=int)
    p.add_argument("--max-tiles", type=int, help="只跑前 N 个 tile，用于测速")
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def main() -> None:
    a = parse_args()
    if not 1 <= a.workers <= 40:
        raise SystemExit("workers 必须在 1~40；建议 16~20")
    if not 256 <= a.tile <= 8192:
        raise SystemExit("tile 必须在 256~8192")
    box = [float(v) for v in a.bbox.replace("，", ",").split(",")]
    if len(box) != 4 or box[0] >= box[2] or box[1] >= box[3]:
        raise SystemExit("bbox 必须是 xmin,ymin,xmax,ymax")
    bands = [v.strip() for v in a.bands.replace("，", ",").split(",") if v.strip()]

    import ee
    import rasterio
    from rasterio.transform import from_origin

    project = os.environ.get("EE_PROJECT")
    if not project:
        raise SystemExit("请先设置环境变量 EE_PROJECT，例如 PowerShell: $env:EE_PROJECT='your-project-id'")
    init = {"project": project}
    if a.endpoint == "high-volume":
        init["opt_url"] = "https://earthengine-highvolume.googleapis.com"
    ee.Initialize(**init)

    roi = ee.Geometry.Rectangle(box)
    asset_id = None
    if a.image:
        image = ee.Image(a.image).select(bands)
        count = 1
        label = a.image
        asset_id = a.image
    else:
        if not (a.start_date and a.end_date) and (a.year is None or a.month is None):
            raise SystemExit("collection 必须同时给 year 和 month")
        collection = ee.ImageCollection(a.collection).filterBounds(roi)
        if a.start_date or a.end_date:
            if not a.start_date or not a.end_date:
                raise SystemExit("--start-date 和 --end-date 必须同时提供")
            col = collection.filterDate(a.start_date, a.end_date).select(bands)
        else:
            col = (collection
                   .filter(ee.Filter.calendarRange(a.year, a.year, "year"))
                   .filter(ee.Filter.calendarRange(a.month, a.month, "month"))
                   .select(bands))
        count = col.size().getInfo()
        if not count:
            raise SystemExit("指定区域和年月没有影像")
        if a.reducer == "median":
            image = col.median()
        elif a.reducer == "mosaic":
            image = col.mosaic()
        else:
            image = ee.Image(col.first())
        period = f"{a.start_date}_{a.end_date}" if a.start_date else f"{a.year}_{a.month:02d}"
        label = f"{a.collection}_{period}_{a.reducer}"

    # 集合归约通常变成 double；float32 足够表达普通反射率，且减少传输量。
    dtype = a.dtype
    if dtype == "auto":
        if a.collection:
            dtype = "float32"
        else:
            # 自动保留常见资产的数值语义，避免浮点指数/NDVI被错误写成整数。
            try:
                type_info = image.bandTypes().getInfo()
                type_text = str(type_info).lower()
                if "double" in type_text or "float" in type_text:
                    dtype = "float32"
                elif "int16" in type_text or "int32" in type_text:
                    dtype = "int16"
                else:
                    dtype = "uint16"
            except Exception:
                dtype = "uint16"
    image = getattr(image, {"float32": "toFloat", "int16": "toInt16",
                            "uint16": "toUint16"}[dtype])()

    # computePixels/getPixels 的 48 MiB 限制按未压缩响应计算；GEO_TIFF
    # 还会携带每波段 mask/额外样本，不能只按 dtype 字节数估算。
    bytes_per = 4 if dtype == "float32" else 2
    safe_bytes = 45 * 2**20
    if a.auto_tile:
        a.tile = max(256, min(4096, int(math.sqrt(
            safe_bytes / (len(bands) * (bytes_per + 1))))))

    if a.affine:
        affine = [float(v) for v in a.affine.replace("，", ",").split(",")]
        if len(affine) != 6 or not a.grid_width or not a.grid_height:
            raise SystemExit("--affine 必须配合 --grid-width/--grid-height")
        width, height = a.grid_width, a.grid_height
        dx, _, x_origin, _, dy, y_origin = affine
    elif a.auto_grid:
        # 自动使用首波段原生投影；旋转网格或投影转换失败时安全回退。
        try:
            pinfo = image.select([bands[0]]).projection().getInfo()
            native_crs = pinfo.get("crs")
            tr = pinfo.get("transform")
            if not native_crs or not tr or len(tr) != 6:
                raise ValueError("影像没有可读取的原生仿射投影")
            if abs(float(tr[1])) > 1e-9 or abs(float(tr[3])) > 1e-9:
                raise ValueError("旋转网格暂不自动裁剪")
            # Geometry.transform 的 getInfo 仍可能返回经纬度几何；用本地 PROJ
            # 明确计算 bbox，避免把大区域错误算成 1x1 像元。
            corners = [(box[0], box[1]), (box[0], box[3]),
                       (box[2], box[1]), (box[2], box[3])]
            try:
                from rasterio.warp import transform as warp_transform
                tx, ty = warp_transform("EPSG:4326",
                                        native_crs,
                                        [point[0] for point in corners],
                                        [point[1] for point in corners])
                projected = list(zip(tx, ty))
            except Exception:
                # Rasterio 无法识别 SR-ORG 等私有/旧投影时，交给显式 --affine。
                raise ValueError(f"Rasterio 无法转换原生 CRS {native_crs}，请提供 --affine")
            xs = [float(point[0]) for point in projected]
            ys = [float(point[1]) for point in projected]
            sx, sy = float(tr[0]), float(tr[4])
            if sx == 0 or sy == 0:
                raise ValueError("无效的原生像元尺寸")
            col0 = math.floor((min(xs) - float(tr[2])) / sx)
            col1 = math.ceil((max(xs) - float(tr[2])) / sx)
            if sy < 0:
                row0 = math.floor((max(ys) - float(tr[5])) / sy)
                row1 = math.ceil((min(ys) - float(tr[5])) / sy)
            else:
                row0 = math.floor((min(ys) - float(tr[5])) / sy)
                row1 = math.ceil((max(ys) - float(tr[5])) / sy)
            width, height = max(1, col1 - col0), max(1, row1 - row0)
            affine = [sx, 0, float(tr[2]) + col0 * sx,
                      0, sy, float(tr[5]) + row0 * sy]
            a.crs_code = native_crs
            print(f"auto-grid=native {native_crs}, window={width}x{height}")
        except Exception as exc:
            print(f"auto-grid fallback: {exc}")
            a.auto_grid = False
    if not a.affine and not a.auto_grid:
        lat = (box[1] + box[3]) / 2
        dx = a.scale / (111320.0 * math.cos(math.radians(lat)))
        dy = -a.scale / 110574.0
        x_origin, y_origin = box[0], box[3]
        affine = [dx, 0, x_origin, 0, dy, y_origin]
        width = max(1, round((box[2] - box[0]) / dx))
        height = max(1, round((box[3] - box[1]) / abs(dy)))
    nx = math.ceil(width / a.tile)
    ny = math.ceil(height / a.tile)
    jobs = [(x, y) for y in range(ny) for x in range(nx)]
    if a.max_tiles:
        jobs = jobs[:a.max_tiles]
    total = len(jobs)
    if a.tile * a.tile * len(bands) * (bytes_per + 1) > 48 * 2**20:
        raise SystemExit("tile 太大，超过 computePixels 官方 48 MB 未压缩限制")

    a.output.parent.mkdir(parents=True, exist_ok=True)
    if a.output.exists() and not a.overwrite:
        raise SystemExit(f"输出已存在：{a.output}；请加 --overwrite")
    print(f"image={label}, images={count}, reducer={a.reducer if a.collection else 'asset'}")
    print(f"grid={width}x{height}, tiles={nx}x{ny}={total}, dtype={dtype}")
    print(f"tile={a.tile}, workers={a.workers}, endpoint={a.endpoint}")

    # 先创建文件并按窗口写入；中途失败不会留下“成功”标记。
    output_crs = None if a.crs_code.startswith("SR-ORG:") else a.crs_code
    profile = dict(driver="GTiff", width=width, height=height, count=len(bands),
                   dtype=dtype, crs=output_crs,
                   transform=rasterio.Affine(*affine),
                   compress="deflate", predictor=2, tiled=True,
                   blockxsize=256, blockysize=256)
    done = 0
    started = time.perf_counter()

    def fetch(job: tuple[int, int]):
        tx, ty = job
        xoff, yoff = tx * a.tile, ty * a.tile
        w, h = min(a.tile, width - xoff), min(a.tile, height - yoff)
        params = {
            "expression": image,
            # 直接让 GEE 返回 GeoTIFF tile，避免 NUMPY_NDARRAY 的结构化转换。
            "fileFormat": "GEO_TIFF",
            "bandIds": bands,
            "grid": {"dimensions": {"width": w, "height": h},
                     "affineTransform": {"scaleX": affine[0], "shearX": affine[1],
                                          "translateX": affine[2] + xoff * affine[0] + yoff * affine[1],
                                          "shearY": affine[3],
                                          "scaleY": affine[4],
                                          "translateY": affine[5] + xoff * affine[3] + yoff * affine[4]},
                     "crsCode": a.crs_code},
        }
        for attempt in range(6):
            try:
                if asset_id:
                    # 已存在资产直接取像元，不重新计算 expression。
                    request = dict(params)
                    request.pop("expression", None)
                    request["assetId"] = asset_id
                    return tx, ty, ee.data.getPixels(request)
                return tx, ty, ee.data.computePixels(params)
            except Exception as exc:  # service throttling/transient network errors
                if attempt == 5:
                    raise RuntimeError(f"tile {tx},{ty} failed: {exc}") from exc
                delay = min(20.0, 0.8 * (2 ** attempt)) + random.random() * 0.4
                time.sleep(delay)

    with rasterio.open(a.output, "w", **profile) as dst:
        with futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
            pending = {pool.submit(fetch, job): job for job in jobs}
            for future in futures.as_completed(pending):
                tx, ty, tile_bytes = future.result()
                xoff, yoff = tx * a.tile, ty * a.tile
                with rasterio.io.MemoryFile(tile_bytes) as mem:
                    with mem.open() as tile_ds:
                        w, h = tile_ds.width, tile_ds.height
                        for band_no in range(1, len(bands) + 1):
                            dst.write(tile_ds.read(band_no), band_no,
                                      window=rasterio.windows.Window(xoff, yoff, w, h))
                done += 1
                elapsed = time.perf_counter() - started
                print(f"{done}/{total} tiles, {done/total:.1%}, {elapsed:.1f}s", flush=True)

    elapsed = time.perf_counter() - started
    print(f"完成：{elapsed:.2f}s，输出 {a.output}")


if __name__ == "__main__":
    main()
