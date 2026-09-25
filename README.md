# GeeFast

GeeFast 是一个面向 Google Earth Engine 遥感数据的高速本地下载 Skill/插件，兼容 Claude Code 与 Codex 的 `skills/` 目录结构。

## 一键安装

在 Claude Code 或 Codex 中添加 GeeFast 插件市场：

```text
/plugin marketplace add https://github.com/xulogin/GeeFast.git
/plugin install geefast@geefast
```

安装后直接说：

> 把这个 GEE 影像下载到本地，自动选择最快方案，并显示进度。

GeeFast 会自动判断单景 `getPixels`、合成结果 `computePixels` 或 geedim 路线，选择原生投影、自动安全切块、合法并发和本地 GeoTIFF 写入。

主推路线是原生 `getPixels` / `computePixels`，不依赖 geedim，也不要求使用传统 `Export.image.toDrive`；这样可以把 GEE 像元请求、瓦片并发、进度和本地写盘直接控制在一条链路里。

## 安全边界

`getPixels` / `computePixels` 的 48 MiB 是单个交互式请求的未压缩数据上限；大影像通过合法切块并发下载，不使用多账号规避配额。认证和项目 ID只从本机环境读取，不写入仓库。

## 运行环境

Windows、Python 3.10+、`earthengine-api`、`rasterio`；使用 geedim 时再安装 `geedim`。运行前设置 `EE_PROJECT`，不要把真实项目号写进脚本。
