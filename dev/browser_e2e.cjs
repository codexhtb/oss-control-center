/* Real Chromium smoke test for the isolated MGMT HTTPS dev stack. */
const fs = require('node:fs')
const { chromium } = require('playwright')

const origin = 'https://172.17.7.2:8443'
const users = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))
users.bezhan = fs.readFileSync('/run/bezhan-password', 'utf8')

async function createUnlinkedTicket(page, kind) {
  await page.getByRole('navigation', { name: 'Основная навигация' }).getByRole('link', { name: 'Заявки', exact: true }).click()
  await page.getByRole('heading', { name: 'Заявки' }).waitFor()
  await page.getByRole('link', { name: 'Новая заявка' }).click()
  if (kind === 'INTERNAL') await page.getByRole('button', { name: /Внутренняя/ }).click()
  const subject = `Синтетическая ${kind} ${Date.now()}`
  await page.locator('#ticket-subject').fill(subject)
  await page.locator('#ticket-description').fill('Проверка создания заявки через Chromium и HTTP API.')
  const responsePromise = page.waitForResponse((response) =>
    response.url().endsWith('/api/v1/tickets') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Создать заявку' }).click()
  const response = await responsePromise
  if (response.status() !== 201) throw new Error(`${kind}: ticket creation HTTP ${response.status()}`)
  await page.getByText(subject).waitFor()
}

async function checkRole(browser, login, role) {
  const context = await browser.newContext({ ignoreHTTPSErrors: false })
  try {
    const page = await context.newPage()
    await page.goto(origin, { waitUntil: 'networkidle' })
    await page.getByRole('heading', { name: 'Вход в систему' }).waitFor()
    const usernameField = page.locator('input#username[name="username"][autocomplete="username"]')
    const passwordField = page.locator('input#current-password[name="password"][autocomplete="current-password"]')
    if (await page.locator('form[action="/api/v1/auth/login"][method="post"]').count() !== 1) {
      throw new Error('Login form is not recognizable by the browser password manager')
    }
    await usernameField.fill(login)
    await passwordField.fill(users[login])
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
    if (login === 'dev-operator') {
      await createUnlinkedTicket(page, 'MANUAL')
      await createUnlinkedTicket(page, 'INTERNAL')
    }
    if (login === 'bezhan') {
      const me = await context.request.get(`${origin}/api/v1/auth/me`)
      const body = await me.json()
      if (body.user.role !== 'engineer' || body.permissions.includes('identity.manage')) {
        throw new Error('Dev engineer role or permissions mismatch')
      }
    }
    await page.getByRole('button', { name: 'Выйти' }).click()
    await page.getByRole('heading', { name: 'Вход в систему' }).waitFor()
    if (login === 'bezhan') {
      await usernameField.fill(login)
      await passwordField.fill(users[login])
      await page.getByRole('button', { name: 'Войти' }).click()
      await page.getByRole('heading', { name: /Добрый день/ }).waitFor()
    }
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
      ['bezhan', 'engineer'],
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
