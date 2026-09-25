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

## WeChatPub 实际执行链路（Windows）

公众号文章不能只停留在 Markdown。标准交付链路是：

1. 复制参考文章和素材到工作区，不修改用户原始公众号项目目录。
2. 运行 `zh_punctuation_fix.py --write`，修复中文正文半角标点。
3. 使用 `format.py --input ... --theme interview --output ... --no-open` 生成 `article.html`、`preview.html` 和 `images/`。
4. 检查 HTML：图片引用存在、样式内联、无 `<script>`、无外链 CSS、无本机绝对路径。
5. 准备 1:1 PNG 封面；正文图片和封面都必须是本地文件。
6. 使用 `publish.py --dir ... --cover ... --title ... --author ... --yes` 上传到微信公众号草稿箱。
7. 只验证草稿创建成功，最后由用户在公众号后台检查并点击发布；禁止脚本群发。

新版 WeChatPub 配置优先读取 `WECHATPUB_CONFIG`，其次是 `~/.wechatpub/config.json`。凭据只读使用，绝不复制进 Git、文章目录或日志。若发布失败，保留脱敏日志并优先检查公众号 IP 白名单、草稿箱接口权限和封面图。

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
