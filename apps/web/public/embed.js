/* 电商 AI 套件 · 可嵌入客服加载器（第 45b 刀）
 *
 * 宿主一行接入：
 *   <script src="https://<控制台地址>/embed.js" data-label="在线客服"></script>
 *
 * 做三件事，全部无依赖、无构建：
 *   1. 在 Shadow DOM 里放一个圆形启动钮（样式与宿主页面完全隔离，互不污染）；
 *   2. 首次点按时才创建 iframe 指向 <控制台>/widget（懒加载，不给宿主首屏加负担）；
 *   3. postMessage 开关：widget 内的「关闭」发消息回来收起面板。
 *
 * 访客身份：在**宿主域**的 localStorage 里生成一枚 uuid（第一方），随 iframe URL
 * 传给 widget，由 widget 在建会话时带给服务端——商家能用自己这边的访客标识对账。
 * 关闭 localStorage（隐私模式）时不阻断加载器，只是没有访客标识。
 *
 * 安全：嵌入是否获准由**服务端白名单**判定（WIDGET_ALLOWED_ORIGINS，非白名单 403）；
 * 本文件只是加载器，不含任何密钥。
 */
;(function () {
  'use strict'

  // 幂等：同一页被加载两次（两个 script 标签 / 被别的脚本再注入）只生效一次，
  // 不重复插启动钮与消息监听（review P2）
  if (window.__ecomAiWidgetLoaded) return
  window.__ecomAiWidgetLoaded = true

  var script = document.currentScript
  if (!script) return

  var CONSOLE_ORIGIN = (script.getAttribute('data-origin') || new URL(script.src).origin).replace(/\/$/, '')
  var LABEL = script.getAttribute('data-label') || '在线客服'
  var VISITOR_KEY = 'ecom_ai_visitor_id'
  var WIDGET_URL = CONSOLE_ORIGIN + '/widget'
  var OPEN_W = 380
  var OPEN_H = 560

  /** 第一方访客 id：宿主域下生成一次、此后复用。storage 不可用时返回空串。 */
  function visitorId() {
    try {
      var id = window.localStorage.getItem(VISITOR_KEY)
      if (!id) {
        id =
          window.crypto && window.crypto.randomUUID
            ? window.crypto.randomUUID()
            : 'v-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10)
        window.localStorage.setItem(VISITOR_KEY, id)
      }
      return id
    } catch {
      // 隐私模式/禁用 storage：不阻断加载器，只是没有访客标识
      return ''
    }
  }

  var open = false
  var frame = null

  var host = document.createElement('div')
  host.setAttribute('data-ecom-ai-widget', '')
  // 老浏览器无 attachShadow 时退化为 light DOM：样式可能受宿主页影响，
  // 但功能不缺（现代浏览器都走 Shadow DOM，样式完全隔离）
  var root = host.attachShadow ? host.attachShadow({ mode: 'open' }) : host
  document.body.appendChild(host)

  var style = document.createElement('style')
  style.textContent = [
    ':host{all:initial}',
    '.wrap{position:fixed;right:20px;bottom:20px;z-index:2147483000;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}',
    '.panel{position:absolute;right:0;bottom:64px;width:' + OPEN_W + 'px;height:' + OPEN_H + 'px;',
    'max-width:calc(100vw - 32px);max-height:calc(100vh - 108px);background:#fff;border:1px solid rgba(0,0,0,.1);',
    'border-radius:10px;overflow:hidden;box-shadow:0 12px 32px rgba(16,24,40,.18);display:none}',
    '.wrap[data-open="1"] .panel{display:block}',
    'iframe{width:100%;height:100%;border:0;display:block}',
    '.launcher{display:flex;align-items:center;gap:8px;height:48px;padding:0 18px;border:0;border-radius:9999px;',
    'background:#0f1115;color:#fff;font-size:14px;line-height:1;cursor:pointer;box-shadow:0 6px 18px rgba(16,24,40,.22)}',
    '.launcher:hover{background:#43454a}',
    '.launcher:focus-visible{outline:2px solid #4176e6;outline-offset:2px}',
    '.launcher[disabled]{opacity:.6;cursor:default}',
  ].join('')

  var wrap = document.createElement('div')
  wrap.className = 'wrap'
  wrap.setAttribute('data-open', '0')

  var launcher = document.createElement('button')
  launcher.className = 'launcher'
  launcher.type = 'button'
  launcher.textContent = LABEL
  launcher.setAttribute('aria-expanded', 'false')

  var panel = document.createElement('div')
  panel.className = 'panel'

  wrap.appendChild(panel)
  wrap.appendChild(launcher)
  root.appendChild(style)
  root.appendChild(wrap)

  /** 首次点按才注入 iframe：宿主首屏不为客服付任何代价。 */
  function ensureFrame() {
    if (frame) return
    frame = document.createElement('iframe')
    frame.setAttribute('title', LABEL)
    frame.setAttribute('allow', 'clipboard-write')
    var visitor = visitorId()
    frame.src = WIDGET_URL + (visitor ? '?visitor=' + encodeURIComponent(visitor) : '')
    panel.appendChild(frame)
  }

  function setOpen(next) {
    open = next
    if (open) ensureFrame()
    wrap.setAttribute('data-open', open ? '1' : '0')
    launcher.setAttribute('aria-expanded', open ? 'true' : 'false')
  }

  launcher.addEventListener('click', function () {
    setOpen(!open)
  })

  // 只认来自控制台 origin 的消息（绝不用 targetOrigin '*'，也不收任意父页消息）
  window.addEventListener('message', function (event) {
    if (event.origin !== CONSOLE_ORIGIN) return
    var data = event.data
    if (!data || data.type !== 'ecom-ai-widget') return
    if (data.action === 'close') setOpen(false)
    if (data.action === 'open') setOpen(true)
  })

  // 宿主也能用代码开合（例如「联系我们」按钮）
  window.EcomAiWidget = {
    open: function () { setOpen(true) },
    close: function () { setOpen(false) },
    toggle: function () { setOpen(!open) },
  }
})()
