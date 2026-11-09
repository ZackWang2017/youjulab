(() => {
  'use strict';
  const appUrl = 'https://app.youjulab.com';
  const page = document.body.dataset.page;
  const brand = '<img src="assets/youju-logo.png" alt="" width="39" height="44"><span class="brand-name">有据<span class="brand-en">youjulab</span></span>';
  const links = [['home', 'index.html', '首页'], ['features', 'features.html', '平台能力'], ['workflow', 'workflow.html', '研究工作流']];
  const header = document.getElementById('site-header');
  header.innerHTML = `<div class="header-inner"><a class="brand" href="index.html" aria-label="有据 youjulab 首页">${brand}</a><button class="menu-toggle" type="button" aria-label="打开导航菜单" aria-controls="main-navigation" aria-expanded="false"><i data-lucide="menu"></i></button><nav class="header-nav" id="main-navigation" aria-label="主导航">${links.map(([key, href, title]) => `<a href="${href}"${page === key ? ' aria-current="page"' : ''}>${title}</a>`).join('')}<a class="button small primary" href="${appUrl}">进入有据<i data-lucide="arrow-up-right"></i></a></nav></div>`;
  document.getElementById('site-footer').innerHTML = `<div class="footer-inner wrap"><div class="footer-main"><div><a class="brand" href="index.html" aria-label="有据 youjulab 首页">${brand}</a><p class="footer-tagline">个人投资研究平台 · 长期积累，独立判断。</p></div><nav class="footer-links" aria-label="页脚导航"><a href="features.html">平台能力</a><a href="workflow.html">研究工作流</a><a href="${appUrl}">进入系统 ↗</a></nav></div><div class="footer-bottom"><p>© ${new Date().getFullYear()} 有据 youjulab · www.youjulab.com</p><p>仅为研究工具，不构成投资建议。示例内容仅作功能展示，不代表真实财务数据或投资收益。</p></div></div>`;

  const toggle = header.querySelector('.menu-toggle');
  const nav = header.querySelector('.header-nav');
  function closeMenu() {
    nav.classList.remove('is-open');
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-label', '打开导航菜单');
  }
  toggle.addEventListener('click', () => {
    const open = !nav.classList.contains('is-open');
    nav.classList.toggle('is-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    toggle.setAttribute('aria-label', open ? '关闭导航菜单' : '打开导航菜单');
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && nav.classList.contains('is-open')) { closeMenu(); toggle.focus(); }
  });
  document.addEventListener('click', event => { if (!header.contains(event.target)) closeMenu(); });
  matchMedia('(min-width: 761px)').addEventListener('change', event => { if (event.matches) closeMenu(); });

  const panel = document.getElementById('tool-panel');
  if (panel) {
    const tools = {
      pdf: {
        title: '原文与思考，放在一起。',
        description: '让 PDF 从“打开看一遍”，变成可以高亮、追问、翻译和记录的研究现场。关联笔记与原始资产一起留存。',
        points: ['连续阅读、高亮、批注与页码定位', '关联富文本笔记，整理大纲与选文摘要', '英中对照与划词翻译，结合专业上下文'],
        preview: '<div class="mini-preview-top"><span><i data-lucide="book-open"></i>原文 × 研究笔记</span><span>能力示意</span></div><img src="assets/workspace-preview.png" alt="财报与关联研究笔记并排展示" width="1600" height="960">'
      },
      document: {
        title: '把看过的，写成想清楚的。',
        description: '用富文本承载研究观点，而不仅是摘抄。标题、层级列表、表格和批注，让长文也有清楚的脉络。',
        points: ['多级标题、层级列表与文本格式', '研究表格、高亮与批注', '按项目积累，持续修改与保存'],
        preview: '<div class="mini-preview-top"><span><i data-lucide="file-text"></i>商业模式研究</span><span>示例文档</span></div><div class="mini-document"><span class="overline">INVESTMENT THESIS / 研究判断</span><h3>好的生意，如何创造价值？</h3><h4>一、先理解客户价值</h4><p>客户为什么付费？产品能否持续解决真实问题？</p><h4>二、再检验竞争优势</h4><ul><li>优势的来源：产品、渠道、规模或网络</li><li>需要验证：定价权与资本回报的持续性</li></ul><div class="mini-quote">把事实、假设和待验证的问题分开记录。</div></div>'
      },
      sheet: {
        title: '把关键假设，摆到桌面上。',
        description: '表格承载历史数据，也承载你对未来的假设。用公式和图表推演情景，不把一个估值数字当成全部答案。',
        points: ['用单元格和公式组织分析模型', '基于数据与描述辅助生成图表', '围绕当前表结构与 AI 协作'],
        preview: '<div class="mini-preview-top"><span><i data-lucide="table-2"></i>估值假设与情景分析</span><span>示例数据</span></div><div class="mini-sheet"><table><thead><tr><th>关键假设</th><th>谨慎</th><th>基准</th><th>乐观</th></tr></thead><tbody><tr><td>收入增长率</td><td>5%</td><td>10%</td><td>15%</td></tr><tr><td>经营利润率</td><td>18%</td><td>22%</td><td>26%</td></tr><tr><td>资本成本</td><td>10%</td><td>9%</td><td>8%</td></tr><tr><td>关注重点</td><td>下行风险</td><td>合理预期</td><td>兑现条件</td></tr></tbody></table><p class="table-note">仅为结构示意，不对应真实企业或投资建议。</p></div>'
      },
      map: {
        title: '看清结构，也看清变化。',
        description: '从业务关系到财务流程，从关键事件到原因拆解。选一种合适的表达方式，让复杂问题更容易被讨论。',
        points: ['思维导图：拆解业务与竞争关系', '时间轴、鱼骨图与财务账户分析', '绘图与层级计划，补全自己的研究方法'],
        preview: '<div class="mini-preview-top"><span><i data-lucide="git-fork"></i>公司价值驱动</span><span>示例导图</span></div><div class="mini-map"><div class="map-root">长期价值<br><small>理解业务的三个问题</small></div><div class="map-branches"><div>增长从哪里来？<small>需求 · 产品 · 市场空间</small></div><div>利润如何留下？<small>竞争 · 定价 · 成本效率</small></div><div>现金怎样分配？<small>再投资 · 分红 · 回购</small></div></div></div>'
      }
    };
    const tabs = Array.from(document.querySelectorAll('[data-tool]'));
    function selectTool(tab, focus = false) {
      const tool = tools[tab.dataset.tool];
      tabs.forEach(button => {
        const active = button === tab;
        button.setAttribute('aria-selected', String(active));
        button.tabIndex = active ? 0 : -1;
      });
      panel.setAttribute('aria-labelledby', tab.id);
      panel.innerHTML = `<div class="tool-panel-layout"><div class="tool-copy"><h2>${tool.title}</h2><p>${tool.description}</p><ul class="tool-points">${tool.points.map(point => `<li><i data-lucide="check"></i>${point}</li>`).join('')}</ul></div><div class="mini-preview">${tool.preview}</div></div>`;
      if (window.lucide) window.lucide.createIcons();
      if (focus) tab.focus();
    }
    tabs.forEach((tab, index) => {
      tab.addEventListener('click', () => selectTool(tab));
      tab.addEventListener('keydown', event => {
        let target;
        if (event.key === 'ArrowRight') target = tabs[(index + 1) % tabs.length];
        if (event.key === 'ArrowLeft') target = tabs[(index - 1 + tabs.length) % tabs.length];
        if (event.key === 'Home') target = tabs[0];
        if (event.key === 'End') target = tabs[tabs.length - 1];
        if (target) { event.preventDefault(); selectTool(target, true); }
      });
    });
    selectTool(tabs[0]);
  }
  if (window.lucide) window.lucide.createIcons();
})();
