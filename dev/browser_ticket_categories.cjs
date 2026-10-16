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

async function createOperator(browser, admin) {
  const username = `category-operator-${Date.now()}`
  const created = await admin.request.post(`${origin}/api/v1/users`, {
    headers: await headers(admin),
    data: { login: username, display_name: 'Синтетический оператор', role: 'operator' },
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
  if (changed.status() !== 200) throw new Error(`Change password: HTTP ${changed.status()}`)
  await login(page, username, password)
  await page.getByRole('heading', { name: /Добрый день/ }).waitFor()
  return { context, page }
}

async function findSubscriber(page) {
  await page.goto(`${origin}/tickets/new`)
  await page.getByRole('textbox', { name: 'Лицевой счёт' }).fill('MOCK-ETH-001')
  const lookup = page.waitForResponse((response) => response.url().endsWith('/api/v1/subscriber-lookups') && response.request().method() === 'POST')
  const active = page.waitForResponse((response) => response.url().includes('/active-tickets') && response.request().method() === 'GET')
  await page.getByRole('button', { name: 'Найти' }).click()
  if ((await lookup).status() !== 200) throw new Error('Synthetic lookup failed')
  if ((await active).status() !== 200) throw new Error('Active ticket lookup failed')
  await page.getByRole('combobox', { name: 'Тема обращения' }).waitFor()
}

async function submit(page) {
  await page.waitForFunction(() => {
    const button = [...document.querySelectorAll('button')].find((item) => item.textContent?.includes('Создать заявку'))
    return button && !button.disabled
  }, null, { timeout: 10000 })
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
    await adminPage.goto(`${origin}/users`)
    await adminPage.getByRole('heading', { name: 'Темы обращений' }).waitFor()
    await adminPage.getByRole('textbox', { name: 'Новая тема' }).fill('Синтетическая тема')
    await adminPage.getByRole('button', { name: 'Добавить тему' }).click()
    await adminPage.getByRole('textbox', { name: 'Название категории Синтетическая тема' }).waitFor()
    const row = adminPage.locator('form.category-editor-row').filter({ has: adminPage.getByRole('textbox', { name: 'Название категории Синтетическая тема' }) })
    await row.getByRole('textbox').fill('Синтетическая тема после изменения')
    await row.getByRole('button', { name: 'Сохранить' }).click()
    await adminPage.getByRole('textbox', { name: 'Название категории Синтетическая тема после изменения' }).waitFor()
    const updatedRow = adminPage.locator('form.category-editor-row').filter({ has: adminPage.getByRole('textbox', { name: 'Название категории Синтетическая тема после изменения' }) })
    await updatedRow.getByRole('checkbox').uncheck()
    const disabled = adminPage.waitForResponse((response) => response.url().includes('/api/v1/ticket-categories/') && response.request().method() === 'PATCH')
    await updatedRow.getByRole('button', { name: 'Сохранить' }).click()
    if ((await disabled).status() !== 200) throw new Error('Disable category failed')

    const operator = await createOperator(browser, admin)
    const page = operator.page
    await findSubscriber(page)
    if (await page.getByRole('option', { name: 'Синтетическая тема после изменения' }).count()) throw new Error('Disabled category is still offered')
    await page.getByRole('combobox', { name: 'Тема обращения' }).selectOption({ label: 'Нет интернета' })
    await page.getByRole('combobox', { name: 'Телефон входящего звонка' }).selectOption({ index: 1 })
    const first = await submit(page)
    await page.getByText('Категория при создании:').waitFor()
    await page.getByText('Телефон входящего звонка:').waitFor()
    if (first.category_name !== 'Нет интернета' || first.caller_phone !== '+0-000-000-0001') throw new Error('Stored category or selected caller phone differs')

    await findSubscriber(page)
    await page.getByRole('heading', { name: 'У абонента уже есть открытые обращения' }).waitFor()
    if (await page.getByRole('button', { name: 'Создать заявку' }).isEnabled()) throw new Error('Duplicate not blocked')
    await page.getByRole('checkbox', { name: /Другая проблема/ }).check()
    await page.getByRole('textbox', { name: /Почему требуется новая заявка/ }).fill('Другая синтетическая проблема')
    await page.getByRole('combobox', { name: 'Тема обращения' }).selectOption({ label: 'Другое' })
    await page.getByRole('textbox', { name: /Уточнение темы/ }).fill('Иной синтетический вопрос')
    const second = await submit(page)
    if (second.caller_phone !== null || second.subject !== 'Иной синтетический вопрос') throw new Error('Optional phone or Other clarification failed')
    console.log('Chromium category admin, linked create, selected/empty caller phone and duplicate guard: passed')
    await operator.context.close()
    await admin.close()
  } finally { await browser.close() }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1 })
