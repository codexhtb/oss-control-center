/* Claim/release Chromium acceptance on a disposable oss_test_* database only. */
const fs = require('node:fs')
const { chromium } = require('playwright')

const origin = 'https://172.17.7.2:8443'

async function login(browser, loginName, password) {
  const context = await browser.newContext({ ignoreHTTPSErrors: false })
  const page = await context.newPage()
  await page.goto(origin)
  await page.locator('#username').fill(loginName)
  await page.locator('#current-password').fill(password)
  await page.getByRole('button', { name: 'Войти' }).click()
  await page.getByRole('heading', { name: /Добрый день/ }).waitFor()
  return { context, page }
}

async function expectMutation(page, path, button, status) {
  const pending = page.waitForResponse((response) => response.url().endsWith(path) && response.request().method() === 'POST')
  await button.click()
  const response = await pending
  if (response.status() !== status) throw new Error(`Unexpected ticket action HTTP ${response.status()}`)
}

async function main() {
  if (!process.env.OSS_DISPOSABLE_DB?.startsWith('oss_test_phase55_browser_')) {
    throw new Error('Refusing browser mutations outside disposable database')
  }
  const users = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))
  const browser = await chromium.launch({ headless: true })
  try {
    const operator = await login(browser, 'dev-operator', users['dev-operator'])
    await operator.page.goto(`${origin}/tickets/new`)
    await operator.page.getByRole('heading', { name: 'Новая заявка' }).waitFor()
    await operator.page.getByRole('button', { name: /Ручная/ }).click()
    await operator.page.locator('#ticket-subject').fill('Синтетическая инженерная очередь')
    await operator.page.locator('#ticket-description').fill('Только одноразовая PostgreSQL база.')
    const createdPending = operator.page.waitForResponse((response) => response.url().endsWith('/api/v1/tickets') && response.request().method() === 'POST')
    await operator.page.getByRole('button', { name: 'Создать заявку' }).click()
    const createdResponse = await createdPending
    if (createdResponse.status() !== 201) throw new Error('Synthetic ticket creation failed')
    const ticket = await createdResponse.json()
    const path = `/api/v1/tickets/${ticket.id}`

    const first = await login(browser, 'dev-engineer', users['dev-engineer'])
    const second = await login(browser, 'queue-engineer-2', users['dev-admin'])
    await first.page.goto(`${origin}/tickets?queue=waiting`)
    await first.page.getByRole('button', { name: /Ожидают инженера 1/ }).waitFor()
    await first.page.locator(`a[href="/tickets/${ticket.id}"].ticket-link`).click()
    await first.page.getByRole('button', { name: 'Взять в работу' }).waitFor()
    await second.page.goto(`${origin}/tickets/${ticket.id}`)
    await second.page.getByRole('button', { name: 'Взять в работу' }).waitFor()

    await expectMutation(first.page, `${path}/claim`, first.page.getByRole('button', { name: 'Взять в работу' }), 200)
    await first.page.getByRole('complementary', { name: 'Управление заявкой' }).getByText('Инженер теста А').waitFor()
    await first.page.getByRole('button', { name: 'Освободить заявку' }).waitFor()
    await expectMutation(second.page, `${path}/claim`, second.page.getByRole('button', { name: 'Взять в работу' }), 409)
    await second.page.getByText('Заявку уже взял другой инженер.', { exact: false }).waitFor()
    await second.page.getByRole('complementary', { name: 'Управление заявкой' }).getByText('Инженер теста А').waitFor()

    await expectMutation(first.page, `${path}/release`, first.page.getByRole('button', { name: 'Освободить заявку' }), 200)
    await first.page.getByRole('button', { name: 'Взять в работу' }).waitFor()
    await second.page.goto(`${origin}/tickets?queue=waiting`)
    await second.page.getByRole('button', { name: /Ожидают инженера 1/ }).waitFor()
    await second.page.locator(`a[href="/tickets/${ticket.id}"].ticket-link`).click()
    await expectMutation(second.page, `${path}/claim`, second.page.getByRole('button', { name: 'Взять в работу' }), 200)
    await second.page.getByRole('complementary', { name: 'Управление заявкой' }).getByText('Инженер теста Б').waitFor()
    await second.page.goto(`${origin}/tickets?queue=mine`)
    await second.page.getByRole('button', { name: /Взяты мной 1/ }).waitFor()
    await second.page.locator(`a[href="/tickets/${ticket.id}"].ticket-link`).waitFor()

    for (const [width, height] of [[1366, 768], [1440, 900], [390, 844]]) {
      await second.page.setViewportSize({ width, height })
      const overflow = await second.page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)
      if (overflow) throw new Error(`Engineer queue overflows at ${width}x${height}`)
    }
    await operator.page.goto(`${origin}/tickets/${ticket.id}`)
    await operator.page.getByRole('heading', { name: `Заявка #${ticket.number}` }).waitFor()
    if (await operator.page.getByRole('button', { name: 'Взять в работу' }).count()) throw new Error('Operator received engineer action')
    const admin = await login(browser, 'dev-admin', users['dev-admin'])
    await admin.page.goto(`${origin}/tickets?queue=waiting`)
    await admin.page.getByRole('button', { name: /Ожидают инженера/ }).waitFor()
    await admin.page.goto(`${origin}/tickets/${ticket.id}`)
    if (await admin.page.getByRole('button', { name: 'Взять в работу' }).count()) throw new Error('Coordinator received engineer claim action')
    console.log('Disposable Chromium: queues, claim conflict, assignee, release, RBAC and three viewports passed')
    await admin.context.close()
    await operator.context.close()
    await first.context.close()
    await second.context.close()
  } finally {
    await browser.close()
  }
}

main().catch((error) => { console.error(`Disposable engineer queue browser check failed: ${error.message}`); process.exitCode = 1 })
