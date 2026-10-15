/* Browser acceptance uses only a disposable oss_test_* PostgreSQL database. */
const fs = require('node:fs')
const crypto = require('node:crypto')
const { chromium } = require('playwright')

const origin = 'https://172.17.7.2:8443'
const adminPassword = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))['dev-admin']
const staffLogin = `delete-test-${Date.now()}`
const changedPassword = `${crypto.randomBytes(24).toString('base64url')}Aa1!`

function expectStatus(response, expected, label) {
  if (response.status() !== expected) throw new Error(`${label}: HTTP ${response.status()}`)
}

async function login(page, username, password) {
  await page.goto(origin, { waitUntil: 'networkidle' })
  await page.getByRole('heading', { name: 'Вход в систему' }).waitFor()
  await page.locator('#username').fill(username)
  await page.locator('#current-password').fill(password)
  await page.getByRole('button', { name: 'Войти' }).click()
  await page.getByRole('heading', { name: /Добрый день/ }).waitFor()
}

async function token(context) {
  const cookie = (await context.cookies(origin)).find((item) => item.name === 'oss_csrf')
  if (!cookie) throw new Error('CSRF cookie missing')
  return cookie.value
}

async function createTicket(page, subject) {
  await page.goto(`${origin}/tickets/new`)
  await page.getByRole('heading', { name: 'Новая заявка' }).waitFor()
  await page.locator('#ticket-subject').fill(subject)
  await page.locator('#ticket-description').fill('Синтетическая заявка во временной базе.')
  const pending = page.waitForResponse((response) => response.url().endsWith('/api/v1/tickets') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Создать заявку' }).click()
  const response = await pending
  expectStatus(response, 201, 'Create test ticket')
  return response.json()
}

async function main() {
  const browser = await chromium.launch({ headless: true })
  try {
    const admin = await browser.newContext({ ignoreHTTPSErrors: false })
    const adminPage = await admin.newPage()
    await login(adminPage, 'dev-admin', adminPassword)
    const foreign = await createTicket(adminPage, 'Чужая тестовая заявка')
    await adminPage.goto(`${origin}/users`)
    await adminPage.getByRole('button', { name: 'Создать пользователя' }).click()
    await adminPage.getByRole('textbox', { name: 'Логин' }).fill(staffLogin)
    await adminPage.getByRole('textbox', { name: 'Имя сотрудника' }).fill('Оператор теста удаления')
    await adminPage.getByRole('checkbox', { name: 'Удаление своих' }).check()
    const createUserPending = adminPage.waitForResponse((response) => response.url().endsWith('/api/v1/users') && response.request().method() === 'POST')
    await adminPage.getByRole('button', { name: 'Создать', exact: true }).click()
    expectStatus(await createUserPending, 201, 'Create test employee')
    const temporaryPassword = await adminPage.locator('.temporary-password').textContent()
    if (!temporaryPassword) throw new Error('Temporary password not shown')

    const staff = await browser.newContext({ ignoreHTTPSErrors: false })
    const staffPage = await staff.newPage()
    await staffPage.goto(origin)
    await staffPage.locator('#username').fill(staffLogin)
    await staffPage.locator('#current-password').fill(temporaryPassword)
    await staffPage.getByRole('button', { name: 'Войти' }).click()
    await staffPage.getByRole('heading', { name: 'Смена временного пароля' }).waitFor()
    await staffPage.locator('input[name="current-password"]').fill(temporaryPassword)
    await staffPage.locator('input[name="new-password"]').fill(changedPassword)
    await staffPage.locator('input[name="repeat-password"]').fill(changedPassword)
    const changed = staffPage.waitForResponse((response) => response.url().endsWith('/api/v1/auth/password'))
    await staffPage.getByRole('button', { name: 'Сменить пароль' }).click()
    expectStatus(await changed, 200, 'Change temporary password')
    await login(staffPage, staffLogin, changedPassword)

    const own = await createTicket(staffPage, 'Своя тестовая заявка')
    await staffPage.getByRole('button', { name: 'Удалить', exact: true }).click()
    await staffPage.getByText(new RegExp(`Удалить заявку №${own.number}`)).waitFor()
    const ownDelete = staffPage.waitForResponse((response) => response.url().endsWith(`/api/v1/tickets/${own.id}`) && response.request().method() === 'DELETE')
    await staffPage.getByRole('button', { name: 'Подтвердить удаление' }).click()
    expectStatus(await ownDelete, 200, 'Delete own ticket')
    expectStatus(await staff.request.get(`${origin}/api/v1/tickets/${own.id}`), 404, 'Deleted ticket hidden')

    const denied = await staff.request.delete(`${origin}/api/v1/tickets/${foreign.id}`, {
      headers: { Origin: origin, 'X-CSRF-Token': await token(staff), 'If-Match': '"1"' }, data: {},
    })
    expectStatus(denied, 403, 'Foreign ticket deletion denied')
    const missingCsrf = await staff.request.delete(`${origin}/api/v1/tickets/${foreign.id}`, {
      headers: { Origin: origin, 'If-Match': '"1"' }, data: {},
    })
    expectStatus(missingCsrf, 403, 'CSRF enforced')

    await adminPage.goto(`${origin}/trash`)
    await adminPage.getByRole('heading', { name: 'Корзина' }).waitFor()
    await adminPage.getByText('Своя тестовая заявка').waitFor()
    const restored = adminPage.waitForResponse((response) => response.url().endsWith(`/api/v1/tickets/${own.id}/restore`))
    await adminPage.getByRole('button', { name: 'Восстановить' }).click()
    expectStatus(await restored, 200, 'Restore ticket')
    expectStatus(await admin.request.get(`${origin}/api/v1/tickets/${own.id}`), 200, 'Restored ticket visible')

    await adminPage.goto(`${origin}/tickets/${foreign.id}`)
    await adminPage.getByRole('button', { name: 'Удалить', exact: true }).click()
    const foreignDelete = adminPage.waitForResponse((response) => response.url().endsWith(`/api/v1/tickets/${foreign.id}`) && response.request().method() === 'DELETE')
    await adminPage.getByRole('button', { name: 'Подтвердить удаление' }).click()
    expectStatus(await foreignDelete, 200, 'Admin delete foreign ticket')
    await adminPage.goto(`${origin}/trash`)
    await adminPage.getByRole('heading', { name: 'Корзина' }).waitFor()
    await adminPage.getByText('Чужая тестовая заявка').waitFor()
    await adminPage.getByRole('button', { name: 'Окончательно удалить' }).click()
    await adminPage.getByRole('textbox', { name: 'Введите номер заявки для подтверждения' }).fill(String(foreign.number))
    await adminPage.getByRole('button', { name: 'Продолжить' }).click()
    const purged = adminPage.waitForResponse((response) => response.url().endsWith(`/api/v1/tickets/${foreign.id}/purge`))
    await adminPage.getByRole('button', { name: 'Да, удалить без возможности восстановления' }).click()
    expectStatus(await purged, 204, 'Purge ticket')
    expectStatus(await admin.request.get(`${origin}/api/v1/tickets/${foreign.id}`), 404, 'Purged ticket absent')
    console.log('Chromium deletion acceptance: own, foreign denial, CSRF, trash, restore, purge passed')
    await staff.close()
    await admin.close()
  } finally {
    await browser.close()
  }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1 })
