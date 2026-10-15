/* Read-only Chromium smoke on the persistent dev database. */
const fs = require('node:fs')
const { chromium } = require('playwright')

const origin = 'https://172.17.7.2:8443'
const password = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))['dev-admin']

async function main() {
  const browser = await chromium.launch({ headless: true })
  try {
    const context = await browser.newContext({ ignoreHTTPSErrors: false })
    const page = await context.newPage()
    await page.goto(origin)
    await page.getByRole('heading', { name: 'Вход в систему' }).waitFor()
    await page.locator('#username').fill('dev-admin')
    await page.locator('#current-password').fill(password)
    await page.getByRole('button', { name: 'Войти' }).click()
    await page.getByRole('heading', { name: /Добрый день/ }).waitFor()
    const userResponse = await context.request.get(`${origin}/api/v1/users`)
    const ticketResponse = await context.request.get(`${origin}/api/v1/tickets`)
    if (userResponse.status() !== 200 || ticketResponse.status() !== 200) throw new Error('Migrated admin permissions failed')
    await page.goto(`${origin}/users`)
    await page.getByRole('heading', { name: 'Пользователи' }).waitFor()
    await page.getByRole('heading', { name: 'Сотрудники' }).waitFor()
    console.log('Persistent dev administrator: login, users UI, tickets API passed')
    await context.close()
  } finally {
    await browser.close()
  }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1 })
