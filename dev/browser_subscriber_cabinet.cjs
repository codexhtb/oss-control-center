/* DEV UI acceptance with synthetic subscriber responses; never reads real secrets. */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const { chromium } = require('playwright')
const origin = 'https://172.17.7.2:9443'
const key = '00000000-0000-4000-8000-000000000001'
const prefix = `/api/v1/subscriber-lookups/${key}`
const stamp = new Date().toISOString()
const fixture = {
  lookup_id: key, source: 'NETUP', fetched_at: stamp,
  expires_at: '2999-01-01T00:00:00Z', is_expired: false,
  subscriber: { subscriber_id: '101', billing_account: '202', full_name: 'Тестовый абонент',
    organization_name: null, phone: null, contacts: [], email: null,
    actual_address: null, house_number: null, field_meta: {} },
  accounts: [], financial_accounts: [], contracts: [], services: [], connections: [],
  technical_state: 'UNKNOWN',
}
async function main() {
  assert.equal(process.env.OSS_DISPOSABLE_DB, 'oss_test_subscriber_cabinet')
  const users = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))
  if (fs.existsSync('/run/dev-admin.json')) {
    const reset = JSON.parse(fs.readFileSync('/run/dev-admin.json', 'utf8'))
    assert.equal(reset.login, 'dev-admin')
    assert.equal(reset.url, origin)
    users[reset.login] = reset.password
  }
  const browser = await chromium.launch({ headless: true })
  try {
    for (const login of ['dev-engineer', 'dev-operator', 'dev-admin']) {
      const context = await browser.newContext({ viewport: { width: 1280, height: 900 },
        permissions: ['clipboard-read', 'clipboard-write'] })
      try {
        const page = await context.newPage()
        const errors = []
        page.on('pageerror', (e) => errors.push(e.name))
        await page.goto(origin)
        await page.locator('#username').fill(login)
        await page.locator('#current-password').fill(users[login])
        await page.getByRole('button', { name: 'Войти' }).click()
        await page.getByRole('heading', { name: /Добро пожаловать/ }).waitFor()
        const permitted = login !== 'dev-operator'
        let secretRequests = 0
        await page.route(`**${prefix}**`, async (route) => {
          const path = new URL(route.request().url()).pathname
          let body
          if (path === prefix) body = fixture
          else if (path === `${prefix}/cabinet`) body = { login: 'synthetic-login', password_available: permitted }
          else if (path === `${prefix}/cabinet/password`) {
            assert.equal(route.request().method(), 'POST')
            assert.ok(route.request().headers()['x-csrf-token'])
            secretRequests++
            body = { password: 'synthetic-secret' }
          } else body = { items: [], total: 0, page: 1, size: 10 }
          await route.fulfill({ status: 200, contentType: 'application/json',
            headers: { 'Cache-Control': 'no-store' }, body: JSON.stringify(body) })
        })
        await page.goto(`${origin}/subscribers/${key}`)
        await page.getByText('synthetic-login', { exact: true }).waitFor()
        assert.equal(secretRequests, 0)
        assert.equal(await page.getByRole('button', { name: 'Копировать пароль', exact: true }).isDisabled(), true)
        if (permitted) {
          await page.getByRole('button', { name: 'Копировать логин', exact: true }).click()
          assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'synthetic-login')
          await page.getByRole('button', { name: 'Показать пароль', exact: true }).click()
          await page.getByText('synthetic-secret', { exact: true }).waitFor()
          assert.equal(secretRequests, 1)
          await page.getByRole('button', { name: 'Копировать пароль', exact: true }).click()
          assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'synthetic-secret')
          await page.getByRole('button', { name: 'Скрыть пароль', exact: true }).click()
          assert.equal(await page.getByText('synthetic-secret', { exact: true }).count(), 0)
          if (login === 'dev-engineer') await page.screenshot({ path: '/run/screenshots/subscriber-cabinet-dev.png', fullPage: true })
        } else assert.equal(await page.getByRole('button', { name: 'Показать пароль', exact: true }).isDisabled(), true)
        assert.deepEqual(errors, [])
        console.log(`PASS DEV ${login.slice(4)}: login, permission, lazy reveal, copy, hide; synthetic only`)
      } finally { await context.close() }
    }
  } finally { await browser.close() }
}
main().catch(() => { console.error('DEV cabinet browser acceptance failed (details suppressed)'); process.exitCode = 1 })
