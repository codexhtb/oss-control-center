/* Callback acceptance uses only the disposable Phase 5.5 runner/database. */
const fs = require('node:fs')
const { chromium } = require('playwright')

const origin = 'https://172.17.7.2:8443'

async function login(browser, name, password) {
  const context = await browser.newContext({ ignoreHTTPSErrors: false })
  const page = await context.newPage()
  await page.goto(origin)
  await page.locator('#username').fill(name)
  await page.locator('#current-password').fill(password)
  await page.getByRole('button', { name: 'Войти' }).click()
  await page.getByRole('heading', { name: /Добрый день/ }).waitFor()
  return { context, page }
}

async function findSubscriber(page) {
  await page.goto(`${origin}/tickets/new`)
  await page.getByRole('button', { name: /По абоненту/ }).click()
  await page.getByRole('textbox', { name: 'Лицевой счёт' }).fill('MOCK-ETH-001')
  const lookup = page.waitForResponse((response) =>
    response.url().endsWith('/api/v1/subscriber-lookups') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Найти' }).click()
  if ((await lookup).status() !== 200) throw new Error('Subscriber lookup failed')
}

async function createLinked(page) {
  await findSubscriber(page)
  await page.getByRole('combobox', { name: 'Тема обращения' }).selectOption({
    label: 'Синтетическая проблема подключения',
  })
  await page.locator('#ticket-description').fill('Одноразовая база Phase 5.7.')
  const pending = page.waitForResponse((response) =>
    response.url().endsWith('/api/v1/tickets') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Создать заявку' }).click()
  const response = await pending
  if (response.status() !== 201) throw new Error(`Create HTTP ${response.status()}`)
  return response.json()
}

async function claimAndResolve(page, ticketId, verdict) {
  const path = `/api/v1/tickets/${ticketId}`
  await page.goto(`${origin}/tickets/${ticketId}`)
  const claim = page.waitForResponse((r) => r.url().endsWith(`${path}/claim`))
  await page.getByRole('button', { name: 'Взять в работу' }).click()
  if ((await claim).status() !== 200) throw new Error('Claim failed')
  await page.getByRole('textbox', { name: 'Решение инженера' }).fill(verdict)
  await page.getByRole('textbox', { name: 'Выполненные действия' }).fill('Синтетическая проверка')
  const resolved = page.waitForResponse((r) => r.url().endsWith(`${path}/resolve`))
  await page.getByRole('button', { name: 'Решено' }).click()
  if ((await resolved).status() !== 200) throw new Error('Resolve failed')
}

async function callback(page, ticketId, button, expectedStatus, comment) {
  const path = `/api/v1/tickets/${ticketId}/callbacks`
  if (comment) await page.getByRole('textbox', { name: 'Комментарий обзвона' }).fill(comment)
  const pending = page.waitForResponse((r) =>
    r.url().endsWith(path) && r.request().method() === 'POST')
  await page.getByRole('button', { name: button }).click()
  const response = await pending
  if (response.status() !== 200) throw new Error(`${button}: HTTP ${response.status()}`)
  if ((await response.json()).status !== expectedStatus) throw new Error(`${button}: wrong status`)
  await page.getByText('Изменение сохранено в заявке.', { exact: true }).waitFor()
}

async function main() {
  if (!process.env.OSS_DISPOSABLE_DB?.startsWith('oss_test_phase55_browser_')) {
    throw new Error('Refusing mutations outside disposable database')
  }
  const users = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))
  const browser = await chromium.launch({ headless: true })
  try {
    const operator = await login(browser, 'dev-operator', users['dev-operator'])
    const engineer = await login(browser, 'dev-engineer', users['dev-engineer'])
    const ticket = await createLinked(operator.page)
    await claimAndResolve(engineer.page, ticket.id, 'Первый синтетический вердикт')

    await operator.page.goto(`${origin}/tickets?queue=callback`)
    await operator.page.getByRole('button', { name: /Решено · обзвон 1/ }).waitFor()
    await operator.page.locator(`a[href="/tickets/${ticket.id}"].ticket-link`).click()
    await operator.page.getByText('Вердикт: Первый синтетический вердикт', { exact: true }).waitFor()
    await callback(operator.page, ticket.id, 'Не дозвонилась', 'RESOLVED')
    await operator.page.getByText('Не дозвонилась', { exact: true }).first().waitFor()
    await callback(operator.page, ticket.id, 'Проблема осталась', 'IN_PROGRESS',
      'Синтетическая проблема сохранилась')

    await engineer.page.goto(`${origin}/tickets?queue=waiting`)
    await engineer.page.locator(`a[href="/tickets/${ticket.id}"].ticket-link`).click()
    await claimAndResolve(engineer.page, ticket.id, 'Повторный синтетический вердикт')
    await operator.page.goto(`${origin}/tickets/${ticket.id}`)
    await operator.page.getByText('Вердикт: Повторный синтетический вердикт', { exact: true }).waitFor()
    await callback(operator.page, ticket.id, 'Проблема устранена', 'CLOSED',
      'Абонент синтетически подтвердил')
    await operator.page.getByText('Проблема устранена', { exact: true }).first().waitFor()
    if (await operator.page.getByRole('button', { name: 'Проблема устранена' }).count()) {
      throw new Error('Closed ticket retained callback actions')
    }

    for (const [width, height] of [[1366, 768], [1440, 900], [390, 844]]) {
      await operator.page.setViewportSize({ width, height })
      if (await operator.page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) {
        throw new Error(`Callback UI overflow at ${width}x${height}`)
      }
    }
    console.log('Disposable Chromium: callback queue, no-answer, return, re-claim, confirmation and history passed')
    await operator.context.close()
    await engineer.context.close()
  } finally {
    await browser.close()
  }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1 })
