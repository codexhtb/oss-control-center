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
      await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin })
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
      const onuResponse = await context.request.get(`${origin}/api/v1/tickets/${ticket.id}/onu`)
      if (onuResponse.status() !== 200) throw new Error('Read-only ONU lookup failed')
      const onu = await onuResponse.json()
      if (onu.availability !== 'AVAILABLE' || onu.candidates.length !== 1 || !onu.match) throw new Error('Ticket 38 lacks its confirmed ONU observation')
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
      const diagnostics = page.locator('section.detail-panel').filter({ has: page.getByRole('heading', { name: 'Диагностика подключения', exact: true }) })
      const candidate = diagnostics.locator('.onu-candidate')
      await candidate.waitFor()
      const item = onu.candidates[0]
      const copyLabel = item.mac ? 'MAC ONU' : 'Serial ONU'
      const copyValue = item.mac || item.serial
      if (!copyValue) throw new Error('ONU has no verified identifier to copy')
      await diagnostics.getByRole('button', { name: `Скопировать ${copyLabel}` }).focus()
      await diagnostics.getByRole('button', { name: `Скопировать ${copyLabel}` }).press('Enter')
      if (await page.evaluate(() => navigator.clipboard.readText()) !== copyValue) throw new Error('Copied ONU identifier differs from source')
      await diagnostics.getByRole('button', { name: 'Скопировать сводку' }).click()
      const copiedSummary = await page.evaluate(() => navigator.clipboard.readText())
      if (!copiedSummary.includes(`ONU: ${item.interface}`) || !copiedSummary.includes(`VLAN: ${onu.match.vlan}`) || !copiedSummary.includes(copyValue)) throw new Error('Copied diagnostic summary differs from source')
      if (item.state === 'ONLINE' && copiedSummary.includes('Причина отключения:')) throw new Error('Old disconnection reason copied as current')
      const refreshedResponse = page.waitForResponse((response) => response.url().endsWith(`/tickets/${ticket.id}/onu`) && response.request().method() === 'GET')
      await diagnostics.getByRole('button', { name: 'Обновить данные' }).click()
      const refresh = await refreshedResponse
      if (refresh.status() !== 200) throw new Error('Read-only ONU refresh failed')
      const refreshedOnu = await refresh.json()
      if (refreshedOnu.availability !== 'AVAILABLE' || refreshedOnu.candidates.length !== 1) throw new Error('ONU source unavailable after refresh')
      await diagnostics.getByRole('status').filter({ hasText: 'Данные обновлены' }).waitFor({ state: 'visible' })
      for (const [label, value] of [['Наблюдение состояния ONU', refreshedOnu.candidates[0].inventory_polled_at], ['Измерение оптики', refreshedOnu.candidates[0].optical_measured_at]]) {
        const row = diagnostics.locator('.onu-source-time').filter({ hasText: label })
        const displayed = value ? value.replace(/^(\d{4})-(\d{2})-(\d{2})[ T]/, '$3.$2.$1 ') : 'Время неизвестно'
        if (!(await row.textContent()).includes(displayed)) throw new Error('Refreshed source time differs from ONU observation')
      }
      const identifiers = candidate.locator('.onu-identifiers')
      const identityText = await candidate.locator('.onu-identity').textContent()
      if (!identityText.includes(item.olt) || !identityText.includes(item.interface)) throw new Error('ONU identity does not match the source')
      for (const expected of [item.mac, item.serial, String(onu.match.vlan)].filter(Boolean)) {
        if (!(await identifiers.textContent()).includes(expected)) throw new Error('ONU identifiers/VLAN do not match the source')
      }
      if (!await identifiers.isVisible()) await identifiers.scrollIntoViewIfNeeded()
      const stateLabel = { ONLINE: 'Online', OFFLINE: 'Offline', DEREGISTERED: 'Deregistered', UNKNOWN: 'UNKNOWN' }[item.state]
      if (await candidate.locator('.onu-state').textContent() !== stateLabel) throw new Error('ONU state differs from its observation')
      if (item.state === 'ONLINE' && await candidate.locator('.onu-disconnect-reason').count()) throw new Error('Old disconnection reason shown for Online')
      for (const [label, value] of [['ONU Rx', item.onu_rx_dbm], ['OLT Rx', item.olt_rx_dbm], ['ONU Tx', item.onu_tx_dbm]]) {
        const metric = candidate.getByRole('group', { name: label, exact: true })
        const text = await metric.textContent()
        if (value === null ? !text.includes('Нет измерения') : !text.includes(value) || !text.includes('dBm')) throw new Error('Optical value/unit differs from the source')
      }
      for (const [label, value] of [['Наблюдение состояния ONU', item.inventory_polled_at], ['Измерение оптики', item.optical_measured_at]]) {
        const row = candidate.locator('.onu-source-time').filter({ hasText: label })
        const expected = value ? value.replace(/^(\d{4})-(\d{2})-(\d{2})[ T]/, '$3.$2.$1 ') : 'Время неизвестно'
        if (!(await row.textContent()).includes(expected)) throw new Error('Source observation time was changed')
      }
      if (onu.limitations.includes('netup_topology_stale')) await diagnostics.getByText('NetUP: историческая лабораторная копия.', { exact: true }).waitFor({ state: 'visible' })
      const oltDetails = diagnostics.locator('details').filter({ has: page.getByText('Проверка OLT в мониторинге', { exact: true }) })
      if (await oltDetails.evaluate((node) => node.open)) throw new Error('OLT checks open by default')
      await oltDetails.locator(':scope > summary').click()
      if (ticket.status !== 'CLOSED') await oltDetails.getByRole('button', { name: 'Проверить OLT', exact: true }).waitFor({ state: 'visible' })
      await oltDetails.locator(':scope > summary').click()
      await diagnostics.screenshot({ path: `/run/screenshots/ticket38-diagnostics-${me.user.role}.png`, mask: [identifiers.locator('strong'), candidate.locator('.onu-identity strong')] })
      for (const width of [820, 390]) {
        await page.setViewportSize({ width, height: 844 })
        await panel.scrollIntoViewIfNeeded()
        const layout = await panel.evaluate((node) => {
          const rect = node.getBoundingClientRect()
          const main = document.querySelector('.ticket-detail-main').getBoundingClientRect()
          return { position: getComputedStyle(node).position, left: rect.left, right: rect.right, bottom: rect.bottom, mainTop: main.top, viewport: window.innerWidth }
        })
        if (layout.position !== 'static' || layout.left < 0 || layout.right > layout.viewport || layout.bottom > layout.mainTop) throw new Error(`Responsive panel layout failed at ${width}`)
        const rx = await candidate.getByRole('group', { name: 'ONU Rx', exact: true }).boundingBox()
        const oltRx = await candidate.getByRole('group', { name: 'OLT Rx', exact: true }).boundingBox()
        if (!rx || !oltRx || Math.abs(rx.y - oltRx.y) > 1 || rx.x >= oltRx.x) throw new Error(`Rx values are not adjacent at ${width}`)
      }
      let subscriberChecked = false
      if (ticket.subscriber_snapshot_id && ticket.connection_snapshot_id) {
        const lookupResponse = await context.request.get(`${origin}/api/v1/subscriber-lookups/${ticket.subscriber_snapshot_id}`)
        if (lookupResponse.status() === 200) {
          const lookup = await lookupResponse.json()
          const connectionIndex = lookup.connections.findIndex((connection) => connection.connection_snapshot_id === ticket.connection_snapshot_id)
          if (connectionIndex < 0) throw new Error('Selected connection absent from Subscriber 360')
          await page.goto(`${origin}/subscribers/${lookup.lookup_id}`)
          const subscriberOnu = page.locator('.subscriber-service').nth(connectionIndex).locator('.onu-section')
          await subscriberOnu.getByRole('button', { name: 'Скопировать сводку' }).waitFor({ state: 'visible' })
          await subscriberOnu.getByRole('button', { name: 'Обновить данные' }).waitFor({ state: 'visible' })
          subscriberChecked = true
        } else if (lookupResponse.status() !== 404) throw new Error('Subscriber 360 read-only lookup failed')
      }
      if (mutationAttempted || pageErrors) throw new Error('Read-only browser check failed')
      console.log(`Ticket 38 read-only Chromium: role=${me.user.role}, state=${ticket.status}, grants, ONU copy/summary/GET refresh, identity/VLAN, Rx/Tx, separate source times, OLT disclosure, desktop sticky, 820/390px layout passed; Subscriber 360=${subscriberChecked ? 'verified' : 'snapshot belongs to another session'}; no ticket mutations`)
      await context.close()
    }
  } finally { await browser.close() }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1 })
