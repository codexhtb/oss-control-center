/* Read-only ticket #38 checks on persistent dev; no ticket mutations. */
const fs = require('node:fs')
const { chromium } = require('playwright')
const origin = 'https://172.17.7.2:8443'

async function main() {
  const browser = await chromium.launch({ headless: true })
  try {
    const users = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))
    for (const [login, password] of [
      ['bezhan', fs.readFileSync('/run/bezhan-password', 'utf8').trim()],
      ['dev-admin', users['dev-admin']],
    ]) {
      const context = await browser.newContext({ ignoreHTTPSErrors: false, viewport: { width: 1600, height: 900 } })
      const page = await context.newPage()
      let mutationAttempted = false
      let pageErrors = 0
      page.on('pageerror', () => pageErrors++)
      await page.route('**/api/v1/tickets**', async (route) => {
        if (route.request().method() !== 'GET') { mutationAttempted = true; await route.abort(); return }
        await route.continue()
      })
      await page.goto(origin)
      await page.locator('#username').fill(login)
      await page.locator('#current-password').fill(password)
      await page.getByRole('button', { name: 'Войти' }).click()
      await page.getByRole('heading', { name: /Добрый день/ }).waitFor()
      const me = await (await context.request.get(`${origin}/api/v1/auth/me`)).json()
      const listing = await context.request.get(`${origin}/api/v1/tickets?number=38`)
      if (listing.status() !== 200) throw new Error('Read-only ticket lookup failed')
      const rows = (await listing.json()).items
      if (rows.length !== 1) throw new Error('Ticket 38 is not unique')
      const ticket = rows[0]
      await page.goto(`${origin}/tickets/${ticket.id}`)
      const panel = page.getByRole('complementary', { name: 'Управление заявкой' })
      await panel.getByRole('heading', { name: 'Заявка #38' }).waitFor()
      const startVisible = await panel.getByRole('button', { name: 'Взять в работу' }).count() === 1
      if (startVisible !== (ticket.status === 'OPEN' && me.permissions.includes('tickets.start'))) throw new Error('Start action RBAC/state mismatch')
      const decisionVisible = await panel.getByRole('button', { name: 'Записать решение' }).count() === 1
      if (decisionVisible !== (ticket.status === 'IN_PROGRESS' && me.permissions.includes('tickets.resolve'))) throw new Error('Decision action RBAC/state mismatch')
      const deletable = me.permissions.includes('tickets.delete.all') || (me.permissions.includes('tickets.delete.own') && ticket.created_by === me.user.id)
      if ((await panel.getByText('Дополнительно', { exact: true }).count() === 1) !== deletable) throw new Error('Delete grant mismatch')
      if (deletable && await panel.getByRole('button', { name: 'Удалить', exact: true }).isVisible()) throw new Error('Delete is not hidden by default')
      const position = await panel.evaluate((node) => {
        window.scrollTo(0, node.getBoundingClientRect().top + window.scrollY + 100)
        return getComputedStyle(node).position
      })
      await page.waitForFunction(() => {
        const rail = document.querySelector('.ticket-action-rail')
        return rail && Math.abs(rail.getBoundingClientRect().top - 20) < 2
      })
      if (position !== 'sticky') throw new Error('Desktop panel is not sticky')
      await panel.screenshot({ path: `/run/screenshots/ticket38-actions-${me.user.role}.png`, mask: [panel.locator('dd')] })
      for (const width of [820, 390]) {
        await page.setViewportSize({ width, height: 844 })
        await panel.scrollIntoViewIfNeeded()
        const layout = await panel.evaluate((node) => {
          const rect = node.getBoundingClientRect()
          const main = document.querySelector('.ticket-detail-main').getBoundingClientRect()
          return { position: getComputedStyle(node).position, left: rect.left, right: rect.right, bottom: rect.bottom, mainTop: main.top, viewport: window.innerWidth }
        })
        if (layout.position !== 'static' || layout.left < 0 || layout.right > layout.viewport || layout.bottom > layout.mainTop) throw new Error(`Responsive panel layout failed at ${width}`)
      }
      if (mutationAttempted || pageErrors) throw new Error('Read-only browser check failed')
      console.log(`Ticket 38 read-only Chromium: role=${me.user.role}, state=${ticket.status}, grants checked, desktop sticky, 820/390px layout passed; no ticket mutations`)
      await context.close()
    }
  } finally { await browser.close() }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1 })
