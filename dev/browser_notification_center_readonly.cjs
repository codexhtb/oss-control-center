/* Read-only acceptance of the live portal. Never acknowledges live notifications. */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const { chromium } = require('playwright')
const origin = 'https://sd.oss-ab.net'
async function main() {
  assert.equal(process.env.OSS_READ_ONLY, '1')
  const users = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))
  const browser = await chromium.launch({ headless: true })
  try {
    for (const login of Object.keys(users)) {
      const context = await browser.newContext({ viewport: { width: 1280, height: 900 } })
      try {
        const page = await context.newPage()
        const errors = []
        page.on('pageerror', (error) => errors.push(error.name))
        await page.goto(origin)
        await page.locator('#username').fill(login)
        await page.locator('#current-password').fill(users[login])
        await page.getByRole('button', { name: 'Войти' }).click()
        await page.getByRole('heading', { name: /Добро пожаловать/ }).waitFor()
        const meResponse = await context.request.get(origin + '/api/v1/auth/me')
        assert.equal(meResponse.status(), 200)
        const me = await meResponse.json()
        const permissions = me.permissions
        const enabled = (permissions.includes('tickets.read.all') || permissions.includes('tickets.read.own'))
          && (permissions.includes('tasks.create') || permissions.includes('tasks.execute')
            || (['admin', 'engineer'].includes(me.user.role) && permissions.includes('tickets.field_work')))
        if (enabled) {
          const response = await context.request.get(origin + '/api/v1/notifications/center')
          assert.equal(response.status(), 200)
          const inbox = await response.json()
          assert.ok(inbox.items.every((item) => !item.read_at))
          assert.ok(inbox.unread_count >= inbox.items.length)
          await page.getByRole('button', { name: /^Уведомления, непрочитанных:/ }).click()
          await page.getByRole('tab', { name: 'Непрочитанные', exact: true }).waitFor()
          const historyResponse = page.waitForResponse((response) => response.url().includes('/notifications/center?state=read') && response.status() === 200)
          await page.getByRole('tab', { name: 'История', exact: true }).click()
          const history = await (await historyResponse).json()
          assert.ok(history.items.every((item) => item.read_at))
          assert.equal(await page.getByRole('alert').count(), 0)
          await page.keyboard.press('Escape')
          assert.equal(await page.getByRole('region', { name: 'Уведомления', exact: true }).count(), 0)
        } else {
          assert.equal(await page.getByRole('button', { name: /^Уведомления, непрочитанных:/ }).count(), 0)
        }
        await page.goto(origin + '/tickets')
        await page.getByRole('heading', { name: /Заявки/, exact: true }).waitFor()
        assert.deepEqual(errors, [])
        console.log(`PASS: live ${me.user.role} portal, permissions, notification tabs and tickets (read-only)`)
      } finally { await context.close() }
    }
  } finally { await browser.close() }
}
main().catch((error) => { console.error(error.message); process.exitCode = 1 })
