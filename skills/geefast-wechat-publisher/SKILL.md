---
name: geefast-wechat-publisher
description: Use when turning a verified remote-sensing download experiment into a Chinese WeChat public-account article, figures, cover prompt, and clean publishable layout.
---

# GeeFast 公众号文章与排版

把已验证的遥感下载实验整理成可发布的中文技术推文。文章要像实验记录，不把估算、局部测速或不同口径的结果混写成结论。

## 工作流程

1. 只读查看用户指定的公众号项目目录、既有文章和素材风格；未经授权不改原目录。
2. 提炼主线：痛点 → 关键限制 → 下载算法 → 实测 → 适用边界 → 复现方式。
3. 事实分成“已完成实测”“进行中进度”“理论/目标估算”三类。
4. 生成原理路线图、速度对比图和下载进度图，标明单位、数据口径和时间点。
5. 生成 Markdown 草稿、素材目录和封面提示词，图片文字使用简体中文。
6. 发布前扫描本机路径、用户名、项目号、token、OAuth、虚拟环境路径和私密数据。

## 写作边界

- 可以写“昆明单景完整下载实测 4.69 秒”。
- 49 GB 级全球数据只能写成经过标注的估算/目标，除非任务已完整结束并记录最终耗时。
- 必须解释 48 MiB 是 `getPixels/computePixels` 单次交互请求限制，不是整景大小限制。
- 不宣传多账号绕配额，不把合法切块并发描述成破解服务限制。
- 公众号文案避免暴露本机绝对路径、账号、项目 ID、凭据和未公开数据。

## 输出结构

```text
公众号草稿/
├── 推文.md
├── 素材/
│   ├── 01_高速下载路线.png
│   ├── 02_速度实测对比.png
│   └── 03_MODIS下载进度.png
└── prompt.md
```
