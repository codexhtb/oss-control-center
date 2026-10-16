/* Mutations are permitted only through run_phase3_browser_e2e.sh on oss_test_*. */
const fs = require('node:fs')
const crypto = require('node:crypto')
const { chromium } = require('playwright')

const origin = 'https://172.17.7.2:8443'
if (!/^oss_test_phase3_browser_[0-9]+$/.test(process.env.OSS_DISPOSABLE_DB || '')) {
  throw new Error('Use the disposable PostgreSQL browser runner')
}
const adminPassword = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))['dev-admin']

async function login(page, username, password) {
  await page.goto(origin)
  await page.locator('#username').fill(username)
  await page.locator('#current-password').fill(password)
  await page.getByRole('button', { name: 'Войти' }).click()
}

async function headers(context) {
  const csrf = (await context.cookies(origin)).find((item) => item.name === 'oss_csrf')
  if (!csrf) throw new Error('CSRF cookie missing')
  return { Origin: origin, 'X-CSRF-Token': csrf.value }
}

async function prepareOperator(browser, admin) {
  const username = `duplicate-operator-${Date.now()}`
  const created = await admin.request.post(`${origin}/api/v1/users`, {
    headers: await headers(admin),
    data: { login: username, display_name: 'Оператор синтетического теста', role: 'operator' },
  })
  if (created.status() !== 201) throw new Error(`Create operator: HTTP ${created.status()}`)
  const temporaryPassword = (await created.json()).temporary_password
  const password = `${crypto.randomBytes(24).toString('base64url')}Aa1!`
  const context = await browser.newContext({ ignoreHTTPSErrors: false })
  const page = await context.newPage()
  await login(page, username, temporaryPassword)
  await page.getByRole('heading', { name: 'Смена временного пароля' }).waitFor()
  const changed = await context.request.post(`${origin}/api/v1/auth/password`, {
    headers: await headers(context),
    data: { current_password: temporaryPassword, new_password: password },
  })
  if (changed.status() !== 200) throw new Error(`Change operator password: HTTP ${changed.status()}`)
  await login(page, username, password)
  await page.getByRole('heading', { name: /Добрый день/ }).waitFor()
  return { context, page }
}

async function findSubscriber(page) {
  await page.goto(`${origin}/tickets/new`)
  await page.getByRole('button', { name: /По абоненту/ }).click()
  await page.getByRole('textbox', { name: 'Лицевой счёт' }).fill('MOCK-ETH-001')
  const pending = page.waitForResponse((response) => response.url().endsWith('/api/v1/subscriber-lookups') && response.request().method() === 'POST')
  const activePending = page.waitForResponse((response) => response.url().includes('/active-tickets') && response.request().method() === 'GET')
  await page.getByRole('button', { name: 'Найти' }).click()
  const response = await pending
  if (response.status() !== 200) throw new Error(`Synthetic lookup: HTTP ${response.status()}`)
  const active = await activePending
  if (active.status() !== 200) throw new Error(`Active tickets: HTTP ${active.status()}`)
}

async function submit(page, subject) {
  await page.locator('#ticket-subject').fill(subject)
  await page.locator('#ticket-description').fill('Синтетическая проблема во временной базе.')
  const pending = page.waitForResponse((response) => response.url().endsWith('/api/v1/tickets') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Создать заявку' }).click()
  const response = await pending
  if (response.status() !== 201) throw new Error(`Create linked ticket: HTTP ${response.status()}`)
  return response.json()
}

async function main() {
  const browser = await chromium.launch({ headless: true })
  try {
    const admin = await browser.newContext({ ignoreHTTPSErrors: false })
    const adminPage = await admin.newPage()
    await login(adminPage, 'dev-admin', adminPassword)
    await adminPage.getByRole('heading', { name: /Добрый день/ }).waitFor()
    const operator = await prepareOperator(browser, admin)
    const page = operator.page
    await findSubscriber(page)
    const first = await submit(page, 'Первое синтетическое обращение')
    await findSubscriber(page)
    await page.getByRole('heading', { name: 'У абонента уже есть открытые обращения' }).waitFor()
    const open = page.getByRole('link', { name: /Открыть существующую заявку/ })
    if (await open.getAttribute('href') !== `/tickets/${first.id}`) throw new Error('Wrong existing ticket link')
    await page.locator('#ticket-description').fill('Другая синтетическая проблема.')
    if (await page.getByRole('button', { name: 'Создать заявку' }).isEnabled()) throw new Error('Duplicate not blocked')
    await page.getByRole('checkbox', { name: /Другая проблема/ }).check()
    if (await page.getByRole('button', { name: 'Создать заявку' }).isEnabled()) throw new Error('Reason not required')
    await page.getByRole('textbox', { name: /Почему требуется новая заявка/ }).fill('Иной синтетический симптом')
    await submit(page, 'Второе синтетическое обращение')
    console.log('Chromium duplicate warning, existing-ticket link and explicit override: passed')
    await operator.context.close()
    await admin.close()
  } finally {
    await browser.close()
  }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1 })
