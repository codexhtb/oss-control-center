/* Real production acceptance. No screenshots, traces, secret files or payload logs. */
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('playwright')

async function main() {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'))
  assert.ok(['https://sd.oss-ab.net', 'https://172.17.7.2:9443'].includes(input.origin))
  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ permissions: ['clipboard-read', 'clipboard-write'] })
  try {
    const page = await context.newPage()
    await page.goto(input.origin)
    await page.locator('#username').fill('cabinet-live-smoke-operator')
    await page.locator('#current-password').fill(input.password)
    await page.getByRole('button', { name: 'Войти' }).click()
    await page.getByRole('heading', { name: /Добро пожаловать/ }).waitFor()
    let count = 0
    const endpoint = `/api/v1/subscriber-lookups/${input.lookup_id}/cabinet/password`
    page.on('request', r => { if (new URL(r.url()).pathname === endpoint) count++ })
    await page.goto(`${input.origin}/subscribers/${input.lookup_id}`)
    const eye = page.getByRole('button', { name: 'Показать пароль', exact: true })
    await eye.waitFor()
    await page.waitForFunction(() => !document.querySelector('[aria-label="Показать пароль"]').disabled)
    assert.equal(count, 0)
    assert.equal(await page.locator('.subscriber-cabinet__secret').textContent(), '••••••••')
    assert.equal(await page.getByRole('button', { name: 'Копировать пароль', exact: true }).isDisabled(), true)
    const login = await page.locator('.subscriber-cabinet__row').first().locator('strong').textContent()
    await page.getByRole('button', { name: 'Копировать логин', exact: true }).click()
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), login)
    const received = page.waitForResponse(r => new URL(r.url()).pathname === endpoint)
    await eye.click()
    const response = await received
    assert.equal(response.status(), 200)
    assert.ok(response.headers()['cache-control'].includes('no-store'))
    const secret = (await response.json()).password
    assert.ok(typeof secret === 'string' && secret.length)
    await page.getByRole('button', { name: 'Скрыть пароль', exact: true }).waitFor()
    assert.equal(await page.locator('.subscriber-cabinet__secret').textContent(), secret)
    await page.getByRole('button', { name: 'Копировать пароль', exact: true }).click()
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), secret)
    await page.getByRole('button', { name: 'Скрыть пароль', exact: true }).click()
    assert.equal(await page.locator('.subscriber-cabinet__secret').textContent(), '••••••••')
    assert.equal(count, 1)
    await page.evaluate(() => navigator.clipboard.writeText(''))
    console.log('PASS operator: HTTPS login, lazy reveal, copy login/password, hide; no secret artifacts')
  } finally {
    await context.close()
    await browser.close()
  }
}
main().catch(() => { console.error('Production operator browser acceptance failed; details suppressed'); process.exit(1) })
