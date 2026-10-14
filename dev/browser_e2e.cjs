/* Real Chromium smoke test for the isolated MGMT HTTPS dev stack. */
const fs = require('node:fs')
const { chromium } = require('playwright')

const origin = 'https://172.17.7.2:8443'
const users = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))

async function checkRole(browser, login, role) {
  const context = await browser.newContext({ ignoreHTTPSErrors: false })
  try {
    const page = await context.newPage()
    await page.goto(origin, { waitUntil: 'networkidle' })
    await page.getByRole('heading', { name: 'Вход в систему' }).waitFor()
    await page.locator('input[name="login"]').fill(login)
    await page.locator('input[name="password"]').fill(users[login])
    await page.getByRole('button', { name: 'Войти' }).click()
    await page.getByRole('heading', { name: /Добрый день/ }).waitFor()
    const cookies = await context.cookies(`${origin}/api/v1/auth/me`)
    const session = cookies.find((cookie) => cookie.name === 'oss_session')
    if (!session || !session.httpOnly || !session.secure || session.sameSite !== 'Strict') {
      throw new Error(`${role}: session cookie policy failed`)
    }
    const tickets = await context.request.get(`${origin}/api/v1/tickets`)
    if (tickets.status() !== (role === 'admin' ? 403 : 200)) {
      throw new Error(`${role}: ticket permission failed`)
    }
    await page.getByRole('button', { name: 'Выйти' }).click()
    await page.getByRole('heading', { name: 'Вход в систему' }).waitFor()
    console.log(`${role}: browser login, workspace, RBAC and logout passed`)
  } finally {
    await context.close()
  }
}

async function main() {
  const browser = await chromium.launch({ headless: true })
  try {
    for (const [login, role] of [
      ['dev-operator', 'operator'],
      ['dev-engineer', 'engineer'],
      ['dev-admin', 'admin'],
    ]) {
      await checkRole(browser, login, role)
    }
  } finally {
    await browser.close()
  }
}

main().catch((error) => {
  console.error(`Browser smoke failed: ${error.message.split('\n')[0]}`)
  process.exitCode = 1
})
