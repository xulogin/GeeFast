---
name: geefast-download
description: Use when a user needs Google Earth Engine remote-sensing imagery downloaded locally, especially large scenes, image collections, composites, progress tracking, or faster alternatives to Drive exports.
---

# GeeFast GEE 下载

把用户要的 GEE 影像可靠地下载到用户指定的本地目录，并优先优化总墙钟时间。

## 先选路线

- 已经存在的单景资产：优先 `getPixels`，直接取资产像元，避免每个瓦片重复计算。
- median、mosaic、first 或其他合成结果：使用 `computePixels`，固定表达式后切块并发。
- 极复杂或超大计算：评估 Batch Export 到 Cloud Storage/Drive，再本地下载；不要把批处理说成绕过配额。

## 必须遵守的边界

- `getPixels` 和 `computePixels` 每个交互式请求有 48 MiB 未压缩数据上限，另有限制单边 32K 像元和 1024 波段；它是单请求边界，不是整景文件边界。
- 不使用多账号、伪造项目或其他规避配额的方法。
- 并发不超过项目允许的交互式请求上限；发生 429 时退避，不盲目加线程。
- 不打印、复制或提交 Earth Engine/GitHub 凭据。
- 只写用户明确指定的输出目录；开始大下载前核对磁盘空间。

## 默认高速策略

1. 使用影像原生投影和原生网格，避免全球数据无谓重投影到 EPSG:4326。
2. 使用 `--auto-tile`，按 45 MiB 安全预算并为掩膜/编码开销预留空间。
3. 请求使用 `GEO_TIFF`，返回后直接写入本地 tiled GeoTIFF，避免 NumPy 结构化转换。
   全球或超过 4 GB 的结果必须启用 BigTIFF，避免经典 TIFF 在本地写盘阶段失败。
4. 单景下载优先尝试 20~32 并发；高容量端点适合大量并行请求，但必须以实际测速为准。
5. 显示已完成瓦片数、百分比、已用时间、文件大小和预计剩余时间。
6. 最终报告区分“已实测”“当前进度”和“估算目标”，不把估算写成完成结果。

## 工具

- `scripts/gee_rest_compute_pixels.py`：原生 REST/客户端 API 下载器，支持单景 `getPixels`、集合 `computePixels`、自动投影、自动类型、自动瓦片、并发和退避。
- `scripts/download_geedim.py`：geedim 下载入口，适合需要 geedim API 的任务。

运行前设置环境变量：

```powershell
$env:EE_PROJECT = 'your-project-id'
```

不要把实际项目号写进仓库文件。认证沿用本机 Earth Engine 客户端凭据，不把凭据复制到项目中。
