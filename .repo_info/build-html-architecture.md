# HTML 文档架构与样式规范

> 适用于 `.repo_info/` 下所有文档页面。公共样式集中在 `common.css`，各页面通过 `<link>` 引用，仅保留页面专属覆盖和内联 JS。

---

## 1. 页面骨架

每个页面必须包含以下元素（按出现顺序）：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Page Title — LLM-Spectro-Agent</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=EB+Garamond:ital,wght@0,500;0,600;1,500&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="../common.css">
</head>
<body>
  <!-- 侧边导航（必须） -->
  <nav class="sidebar" id="sidebar">...</nav>
  <button class="sidebar-toggle" id="sidebar-toggle" aria-label="Toggle navigation" aria-expanded="false">☰</button>
  <div class="sidebar-overlay" id="sidebar-overlay"></div>
  <!-- 光谱装饰条（必须） -->
  <div class="spectral-edge" aria-hidden="true"></div>
  <!-- 主内容区 -->
  <div class="container">
    <!-- 面包屑 / 标题+概览 / 函数树 / 目录 / 详细内容 / 注释 / 前后导航 / 页脚 -->
  </div>
  <script src="../common.js"></script>
  <script>/* 侧边栏 JS（见第 4 节） */</script>
</body>
</html>
```

**字体**：Google Fonts — `EB Garamond`（展示）、`IBM Plex Sans`（正文）、`IBM Plex Mono`（代码/标签）。子页面路径用 `../common.css`，index.html 用 `common.css`。

---

## 2. 公共组件

以下组件由 `common.css` 提供，页面内直接用 HTML 类名即可，无需写 CSS。

### 2.1 全局元素

| 组件 | HTML 模式 |
|---|---|
| 光谱装饰条 | `<div class="spectral-edge" aria-hidden="true"></div>` |
| 面包屑 | `<div class="breadcrumb"><a href="..">Home</a> / ...</div>` |
| 页脚 | `<div class="footer"><p><a href="..">Home</a> &middot; LLM-Spectro-Agent &middot; 2026-06-11</p></div>` |

### 2.2 内容组件

| 组件 | HTML 模式 | 用途 |
|---|---|---|
| 概览框 | `<div class="overview-box">...</div>` | 页面头部信息（路径、依赖、标签） |
| 标签行 | `<div class="tag-row"><span class="tag primary">...</span></div>` | 关键词标签，颜色见第 7 节 |
| 函数树 | `<div class="file-tree"><ul class="tree">...</ul></div>` | 函数/类结构树，支持 `<details>` 折叠 |
| 函数块 | `<div class="func-block" id="..."><h3>name</h3><div class="sig"><code>...</code></div><p>...</p></div>` | 单个函数/方法的详细说明 |
| 类标题 | `<h2 id="ClassName"><span class="class-badge">class</span> ClassName</h2>` | 类声明，后跟 `<p class="desc">` |
| 目录 | `<ol class="toc"><li><a href="#...">...</a></li></ol>` | 页内锚点目录 |
| 表格 | `<table><tr><th>Key</th><th>Type</th><th>Env Var</th><th>Description</th></tr>...</table>` | 字段/参数说明（四列标准格式） |
| 注释框 | `<div class="note"><strong>标题</strong> 内容</div>` | 重要提示/设计说明 |
| 前后导航 | `<div class="prev-next"><a href="...">← Prev</a> · <a href="...">Next →</a></div>` | 上一篇/下一篇链接 |

### 2.3 index.html 专属组件

以下组件仅在主页使用，CSS 同样来自 `common.css`：

| 组件 | HTML 模式 |
|---|---|
| 卡片网格 | `<div class="card-grid"><div class="card"><span class="badge done">Complete</span><h4><a>...</a></h4><div class="path">...</div><div class="desc">...</div><div class="deps">...</div></div></div>` |
| 状态徽章 | `<span class="badge done">Complete</span>` 或 `<span class="badge skel">Skeleton</span>` |
| 统计卡片 | `<div class="stat-grid"><div class="stat-card"><div class="num">5</div><div class="label">Stages</div></div></div>` |
| 分区标题 | `<div class="section-header"><h2>...</h2><span class="count">N modules</span></div><p class="section-intro">...</p>` |
| 英雄统计 | `<div class="hero-stats"><div class="hero-stat"><span class="num">5</span><span class="unit">Stages</span></div></div>` |
| 代码图表 | `<div class="graph">ASCII diagram</div>` |
| 标题副标签 | `<h1><span class="section-label">Core</span>Title</h1>` 或 `<h1><span class="sub">Label</span>FORMA</h1>` |

---

## 3. 侧边导航栏

### 3.1 结构模式

**index.html（顶级页面）**：扁平列表。

```html
<nav class="sidebar" id="sidebar">
  <a href="#" class="sidebar-link" data-label="Page top" id="sidebar-link-top">TOP</a>
  <a href="#pipeline" class="sidebar-link" data-label="Pipeline">Pipeline</a>
  <!-- 更多一级链接 ... -->
</nav>
```

**子页面（含可展开 Contents）**：

```html
<nav class="sidebar" id="sidebar">
  <a href="#" class="sidebar-link" data-label="Page top" id="sidebar-link-top">TOP</a>
  <a href="#function-tree" class="sidebar-link" data-label="Function tree">Tree</a>
  <div class="sidebar-section" id="sidebar-section-contents">
    <a href="#contents" class="sidebar-link sidebar-section-toggle" data-label="Contents">Contents</a>
    <div class="sidebar-sublist" id="sidebar-sublist-contents">
      <a href="#section-a" class="sidebar-link sidebar-sublink" data-label="section_a()">section_a</a>
      <a href="#section-b" class="sidebar-link sidebar-sublink" data-label="section_b()">section_b</a>
    </div>
  </div>
</nav>
```

**规则**：
- `id="sidebar-link-top"` 必须存在，作为回退高亮
- `id="sidebar-sublist-contents"` 和 `id="sidebar-section-contents"` 必须存在，JS 用其控制折叠
- 一级标签用全称，无标记符号。二级标签用全称，前缀实心圆点（CSS `::before` 自动）
- `data-label` 存完整名称，供移动端展开时替换文本
- `#function-tree` 和 `#contents` 对应页内 h2 的 id

### 3.2 侧边栏 JS 模板

每页必须内联 JS（在 `</body>` 前）。自定义：

1. **`contentsSubIds`** — 属于 Contents 区块的所有锚点 id（含 `'contents'` 自身）
2. 无二级导航的页面（index.html, config_overview）省略 `sublistContents`/`contentsSubIds`

参考 `llm.html`、`main.html` 或任一 `config_*.html` 获取完整模板。

核心行为：
- scroll-spy：从下往上找 `top ≤ 120px` → 高亮；触底时放宽为 `top < 屏高`
- 移动端 ☰ 展开全高侧边栏，点击遮罩/链接/Escape 关闭
- Contents 点击立即展开子列表
- 所有非锚点链接自动 `target="_blank"`

---

## 4. index.html 专属样式

`index.html` 在 `<style>` 块中覆盖：

- 正文字号 18px，容器 max-width 1100px
- h1/h2/h3 更大的展示字号，h2::before 用 emission-dim 竖线
- Pipeline 布局全系列：`.pipeline`、`.pipeline-stage`、`.pipeline-stage-inner`、`.pipeline-arena`、`.arena-ha`、`.arena-aa-col`、`.sub-block`、`.connector-*`、`.two-col`
- Module 标签（`.module-label`）：arena-head 内绝对定位在左侧
- 响应式 768px 折叠

子页面通常无需额外 `<style>` 块。

---

## 5. 内容核对清单

- [ ] 路径匹配源代码（`src/FORMA/` 非旧的 `src/AstroAgent/`）
- [ ] 函数签名、类字段、默认值、env var 名与源码一致
- [ ] 不存在的字段/方法已移除
- [ ] 外部链接用相对路径，JS 自动 `target="_blank"`
- [ ] 页内锚点 `#id` 与目标 `id` 一致
- [ ] `Function Tree`、`Contents` h2 有 `id="function-tree"` 和 `id="contents"`

---

## 6. 主题

色彩变量定义在 `common.css` 的 `:root`（暗色）和 `[data-theme="light"]`（亮色）。页面内用 `var(--xxx)` 引用，不硬编码色值。

**暗色**：deep blue-black 背景、amber 标题 (`#eccd70`)、emission 橙强调 (`#ff8868`)、absorption 蓝链接 (`#6ebeff`)。

**亮色**：浅灰背景、crimson 标题 (`#9c0c13`)、蓝色强调和链接。

主题切换按钮由 `common.js` 提供（右下角 ☀/☽），localStorage 记忆，首次跟随 OS 偏好。

---

## 7. Tag 颜色速查

| class | 暗色 | 亮色 |
|---|---|---|
| `tag primary` | 暖橙底 + emission 橙字 | 深蓝底 + emission 蓝字 |
| `tag secondary` | 暖琥珀底 + amber-dim 金字 | 淡蓝底 + absorption 蓝字 |
| `tag muted` | 极淡金底 + faint 灰字 | 极淡灰底 + faint 灰字 |
| `tag green` | green 底 + green 字 | green 底 + green 字 |
