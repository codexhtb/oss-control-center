/* Simplified ticket UI acceptance on the disposable Phase 5.5 database only. */
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

async function main() {
  if (!process.env.OSS_DISPOSABLE_DB?.startsWith('oss_test_phase55_browser_')) {
    throw new Error('Refusing mutations outside disposable database')
  }
  const users = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))
  const browser = await chromium.launch({ headless: true })
  try {
    const operator = await login(browser, 'dev-operator', users['dev-operator'])
    await operator.page.goto(`${origin}/tickets/new`)
    await operator.page.getByRole('button', { name: /Внутренняя/ }).click()
    await operator.page.locator('#ticket-subject').fill('Синтетическая причина решения Phase 5.8')
    await operator.page.locator('#ticket-description').fill('Одноразовая проверка упрощённой карточки.')
    const created = operator.page.waitForResponse((r) =>
      r.url().endsWith('/api/v1/tickets') && r.request().method() === 'POST')
    await operator.page.getByRole('button', { name: 'Создать заявку' }).click()
    const createdResponse = await created
    if (createdResponse.status() !== 201) throw new Error(`Create HTTP ${createdResponse.status()}`)
    const ticket = await createdResponse.json()
    const path = `/api/v1/tickets/${ticket.id}`

    const engineer = await login(browser, 'dev-engineer', users['dev-engineer'])
    const optionalRequests = []
    const requestPath = (url) => new URL(url).pathname
    engineer.page.on('request', (request) => {
      if (['/history', '/manual-diagnostics', '/decisions'].some((suffix) =>
        requestPath(request.url()).endsWith(`${path}${suffix}`))) optionalRequests.push(request.url())
    })
    await engineer.page.goto(`${origin}/tickets/${ticket.id}`)
    await engineer.page.getByRole('heading', { name: `Заявка #${ticket.number}` }).waitFor()
    if (optionalRequests.length) throw new Error('Collapsed sections loaded before disclosure')
    if (await engineer.page.getByRole('textbox', { name: 'Наблюдение инженера' }).count()) {
      throw new Error('Legacy manual observation input is visible')
    }

    const historyResponse = engineer.page.waitForResponse((r) =>
      requestPath(r.url()).endsWith(`${path}/history`))
    await engineer.page.getByText('История событий').click()
    if ((await historyResponse).status() !== 200) throw new Error('History load failed')
    const diagnosticResponse = engineer.page.waitForResponse((r) =>
      requestPath(r.url()).endsWith(`${path}/manual-diagnostics`))
    await engineer.page.getByText('Технические сведения').click()
    if ((await diagnosticResponse).status() !== 200) throw new Error('Diagnostics history load failed')

    const claimed = engineer.page.waitForResponse((r) => r.url().endsWith(`${path}/claim`))
    await engineer.page.getByRole('button', { name: 'Взять в работу' }).click()
    if ((await claimed).status() !== 200) throw new Error('Claim failed')
    await engineer.page.getByRole('combobox', { name: 'Причина решения' }).selectOption('CONSULTATION')
    await engineer.page.getByRole('textbox', { name: 'Решение инженера' })
      .fill('Абоненту предоставлена синтетическая консультация')
    if (await engineer.page.getByRole('textbox', { name: 'Выполненные действия' }).count()) {
      throw new Error('Legacy performed actions input is visible')
    }
    const resolved = engineer.page.waitForResponse((r) => r.url().endsWith(`${path}/resolve`))
    await engineer.page.getByRole('button', { name: 'Решено' }).click()
    const resolvedResponse = await resolved
    if (resolvedResponse.status() !== 200) throw new Error(`Resolve HTTP ${resolvedResponse.status()}`)
    const payload = resolvedResponse.request().postDataJSON()
    if (payload.reason !== 'CONSULTATION' || 'performed_actions' in payload) {
      throw new Error('Resolution payload does not match simplified contract')
    }
    await engineer.page.getByText(/Готова к закрытию без обзвона/).waitFor()

    const decisionsResponse = engineer.page.waitForResponse((r) =>
      requestPath(r.url()).endsWith(`${path}/decisions`))
    await engineer.page.getByText('Исторические решения').click()
    if ((await decisionsResponse).status() !== 200) throw new Error('Decision history load failed')
    await engineer.page.locator('.fact-item__secondary', { hasText: 'Консультация' }).waitFor()
    await engineer.page.getByText('Абоненту предоставлена синтетическая консультация').waitFor()

    for (const [width, height] of [[1366, 768], [1440, 900], [390, 844]]) {
      await engineer.page.setViewportSize({ width, height })
      if (await engineer.page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) {
        throw new Error(`Simplified ticket UI overflow at ${width}x${height}`)
      }
    }
    console.log('Disposable Chromium: lazy details, one comment flow and structured resolution passed')
    await operator.context.close()
    await engineer.context.close()
  } finally {
    await browser.close()
  }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1 })
