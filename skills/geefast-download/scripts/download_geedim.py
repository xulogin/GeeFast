"""用 geedim 将 GEE 影像直接下载为 GeoTIFF。

只在真正执行命令时写入 --output；默认参数偏保守，避免 429 和内存墙。
不创建 Export task，也不处理/打印 Earth Engine 凭据。
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path


def args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="GEE geedim 高速分块下载")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", help="单幅 EE Image ID")
    src.add_argument("--collection", help="EE ImageCollection ID；按年月取中值")
    p.add_argument("--year", type=int, help="collection 年过滤")
    p.add_argument("--month", type=int, help="collection 月过滤")
    p.add_argument("--bbox", required=True, metavar="XMIN,YMIN,XMAX,YMAX")
    p.add_argument("--bands", required=True, help="逗号分隔波段，如 B4,B3,B2")
    p.add_argument("--scale", type=float, required=True, help="输出米/像元")
    p.add_argument("--crs", default="EPSG:4326")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--max-requests", type=int, default=16)
    p.add_argument("--max-tile-dim", type=int, default=1024)
    p.add_argument("--max-tile-size", type=float, default=30.0)
    p.add_argument("--dtype", choices=("auto", "float32", "float64", "int16", "uint16"),
                   default="auto", help="输出类型；集合中值默认 float32")
    p.add_argument("--endpoint", choices=("standard", "high-volume"), default="standard",
                   help="Earth Engine 端点；复杂合成默认 standard")
    p.add_argument("--reducer", choices=("median", "mosaic", "first"), default="median",
                   help="集合处理方式；mosaic/first 更快但不是时间中值")
    p.add_argument("--overwrite", action="store_true")
    return p.parse_args()


def main() -> None:
    a = args()
    if a.max_requests < 1 or a.max_requests > 40:
        raise SystemExit("--max-requests 建议 1~20；不能超过标准端点并发上限 40")
    if a.max_tile_dim < 64 or a.max_tile_dim > 10000:
        raise SystemExit("--max-tile-dim 建议 64~4096，且不能超过 10000")
    if a.max_tile_size <= 0 or a.max_tile_size >= 32:
        raise SystemExit("--max-tile-size 必须小于 32 MB，建议 30")

    import ee
    import aiohttp

    # geedim 使用 aiohttp；国内代理环境下让它读取 HTTPS_PROXY/HTTP_PROXY。
    if not getattr(aiohttp.ClientSession, "_codex_trust_env", False):
        old_init = aiohttp.ClientSession.__init__

        def init_with_env(self, *args, **kwargs):
            kwargs.setdefault("trust_env", True)
            old_init(self, *args, **kwargs)

        aiohttp.ClientSession.__init__ = init_with_env
        aiohttp.ClientSession._codex_trust_env = True

    import geedim  # noqa: F401  导入后给 ee.Image 挂上 .gd 访问器

    project = os.environ.get("EE_PROJECT")
    init_kwargs = {"project": project} if project else {}
    if a.endpoint == "high-volume":
        init_kwargs["opt_url"] = "https://earthengine-highvolume.googleapis.com"
    if init_kwargs:
        ee.Initialize(**init_kwargs)
    else:
        ee.Initialize()

    box = [float(x) for x in a.bbox.replace("，", ",").split(",")]
    if len(box) != 4 or box[0] >= box[2] or box[1] >= box[3]:
        raise SystemExit("--bbox 必须是 xmin,ymin,xmax,ymax")
    region = ee.Geometry.Rectangle(box)
    bands = [x.strip() for x in a.bands.replace("，", ",").split(",") if x.strip()]
    if not bands:
        raise SystemExit("--bands 不能为空")

    if a.image:
        image = ee.Image(a.image).select(bands)
        label = a.image.replace("/", "_")
    else:
        if a.year is None or a.month is None:
            raise SystemExit("使用 --collection 时必须同时提供 --year 和 --month")
        collection = (ee.ImageCollection(a.collection)
                      .filterBounds(region)
                      .filter(ee.Filter.calendarRange(a.year, a.year, "year"))
                      .filter(ee.Filter.calendarRange(a.month, a.month, "month")))
        count = collection.size().getInfo()
        if count == 0:
            raise SystemExit("指定区域/年月没有影像")
        selected = collection.select(bands)
        if a.reducer == "median":
            image = selected.median()
        elif a.reducer == "mosaic":
            image = selected.mosaic()
        else:
            image = ee.Image(selected.first())
        label = f"{a.collection}_{a.year}_{a.month:02d}_{a.reducer}"
        print(f"collection={a.collection}, images={count}, reducer={a.reducer}")

    a.output.parent.mkdir(parents=True, exist_ok=True)
    if a.output.exists() and not a.overwrite:
        raise SystemExit(f"输出已存在：{a.output}；如确认覆盖请加 --overwrite")

    print(f"image={label}")
    print(f"bands={bands}, bbox={box}, scale={a.scale} m, crs={a.crs}")
    print(f"max_requests={a.max_requests}, max_tile_dim={a.max_tile_dim}, "
          f"max_tile_size={a.max_tile_size} MB, endpoint={a.endpoint}")
    print(f"output={a.output}")

    # geedim 会自动把影像切成多个不超过 EE 单请求限制的 tile，并并发取回。
    # median/mean 等集合归约常被 EE 提升为 float64。对反射率等普通连续量，
    # float32 足够且能减少约一半传输字节；单景影像默认保持原类型。
    dtype = a.dtype
    if dtype == "auto" and a.collection:
        dtype = "float32"
    prepare_kwargs = dict(
        region=region,
        scale=a.scale,
        crs=a.crs,
        bands=bands,
    )
    if dtype != "auto":
        prepare_kwargs["dtype"] = dtype
    prepared = image.gd.prepareForExport(**prepare_kwargs)
    prepared.gd.toGeoTIFF(
        file=str(a.output),
        overwrite=a.overwrite,
        max_requests=a.max_requests,
        max_tile_dim=a.max_tile_dim,
        max_tile_size=a.max_tile_size,
    )
    print("完成")


if __name__ == "__main__":
    main()
