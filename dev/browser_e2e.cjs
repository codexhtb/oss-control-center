/* Chromium acceptance against the isolated MGMT HTTPS dev stack. */
const fs = require('node:fs')
const { chromium } = require('playwright')
const origin = 'https://172.17.7.2:8443'
const billingMode = process.env.OSS_BILLING_MODE || 'MOCK'
const users = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))
users.bezhan = fs.readFileSync('/run/bezhan-password', 'utf8')

function expectStatus(response, status, action) {
  if (response.status() !== status) throw new Error(`${action}: HTTP ${response.status()}`)
}
async function postResponse(page, suffix, button, expected = 200) {
  const pending = page.waitForResponse((response) => response.url().endsWith(suffix) && response.request().method() === 'POST')
  await page.getByRole('button', { name: button }).click()
  const response = await pending
  expectStatus(response, expected, suffix)
  return response.json()
}
async function login(browser, username, role) {
  const context = await browser.newContext({ ignoreHTTPSErrors: false })
  const page = await context.newPage()
  await page.goto(origin, { waitUntil: 'networkidle' })
  await page.getByRole('heading', { name: 'Вход в систему' }).waitFor()
  if (await page.locator('form[action="/api/v1/auth/login"][method="post"] input#username[name="username"][autocomplete="username"]').count() !== 1 ||
      await page.locator('form[action="/api/v1/auth/login"][method="post"] input#current-password[name="password"][autocomplete="current-password"]').count() !== 1) {
    throw new Error('Login form lacks browser password manager attributes')
  }
  await page.locator('#username').fill(username)
  await page.locator('#current-password').fill(users[username])
  await page.getByRole('button', { name: 'Войти' }).click()
  await page.getByRole('heading', { name: /Добрый день/ }).waitFor()
  const session = (await context.cookies(`${origin}/api/v1/auth/me`)).find((cookie) => cookie.name === 'oss_session')
  if (!session || !session.httpOnly || !session.secure || session.sameSite !== 'Strict') throw new Error(`${role}: session cookie policy failed`)
  expectStatus(await context.request.get(`${origin}/api/v1/tickets`), role === 'admin' ? 403 : 200, `${role} RBAC`)
  return { context, page }
}
async function csrfToken(context) {
  const cookie = (await context.cookies(`${origin}/api/v1/auth/me`)).find((item) => item.name === 'oss_csrf')
  if (!cookie) throw new Error('CSRF cookie missing')
  return cookie.value
}
async function createTicket(page, kind) {
  await page.getByRole('navigation', { name: 'Основная навигация' }).getByRole('link', { name: 'Заявки', exact: true }).click()
  await page.getByRole('link', { name: 'Новая заявка' }).click()
  if (kind === 'INTERNAL') await page.getByRole('button', { name: /Внутренняя/ }).click()
  if (kind === 'SUBSCRIBER_LINKED') {
    await page.getByRole('button', { name: /По абоненту/ }).click()
    await page.locator('#billing-account').fill('MOCK-GPON-001')
    const pending = page.waitForResponse((response) => response.url().endsWith('/api/v1/subscriber-lookups') && response.request().method() === 'POST')
    await page.getByRole('button', { name: 'Найти' }).click()
    expectStatus(await pending, 200, 'Subscriber lookup')
    await page.getByText('ПОДТВЕРЖДЁННАЯ КАРТОЧКА').waitFor()
    if (await page.getByRole('button', { name: 'Создать заявку' }).isEnabled()) throw new Error('Ambiguous selection was optional')
    await page.getByRole('combobox', { name: 'Услуга' }).selectOption({ index: 1 })
    await page.getByRole('combobox', { name: 'Подключение' }).selectOption({ index: 1 })
  }
  const subject = `Синтетическая ${kind} ${Date.now()}`
  await page.locator('#ticket-subject').fill(subject)
  await page.locator('#ticket-description').fill('Проверка полного workflow через Chromium.')
  const ticket = await postResponse(page, '/api/v1/tickets', 'Создать заявку', 201)
  await page.getByRole('heading', { name: subject }).waitFor()
  if (kind === 'SUBSCRIBER_LINKED' && (!ticket.subscriber_snapshot_id || !ticket.connection_snapshot_id || !ticket.selected_service_id)) throw new Error('Confirmed subscriber link was not persisted')
  if (kind !== 'SUBSCRIBER_LINKED' && (ticket.subscriber_snapshot_id || ticket.account_no)) throw new Error('Unlinked ticket has billing data')
  return ticket
}
async function checkNetupLabLookup(page) {
  const accountNo = process.env.OSS_LAB_ACCOUNT_NO
  if (!accountNo) throw new Error('Protected NetUP Lab smoke account is not configured')
  await page.getByRole('navigation', { name: 'Основная навигация' }).getByRole('link', { name: 'Абоненты', exact: true }).click()
  await page.locator('#subscriber-account').fill(accountNo)
  const pending = page.waitForResponse((response) => response.url().endsWith('/api/v1/subscriber-lookups') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Найти' }).click()
  const response = await pending
  expectStatus(response, 200, 'NetUP Lab lookup')
  const card = await response.json()
  if (card.source !== 'NETUP' || card.subscriber.billing_account !== accountNo) throw new Error('Lookup source is not NetUP Lab')
  await page.locator('.subscriber-heading h1').waitFor()
  if (!page.url().endsWith(`/subscribers/${card.lookup_id}`)) throw new Error('Subscriber route exposes account number or wrong lookup')
  if (!(await page.locator('.subscriber-heading').textContent()).includes('NETUP')) throw new Error('Browser card does not show NetUP source')
  if (await page.getByRole('heading', { name: 'Финансовые сведения' }).count() !== 1) throw new Error('Operator finance section missing')
  if (card.connections.length && !(await page.locator('.ip-group').count())) throw new Error('IP group rows not rendered')
  expectStatus(await page.context().request.get(`${origin}/api/v1/subscriber-lookups/${card.lookup_id}/tickets`), 200, 'Subscriber ticket history')
  console.log('NetUP Lab: full Subscriber 360 card and ticket history passed')
  if (process.env.OSS_STAGE3C_LINKED !== '1') return null
  await page.getByRole('link', { name: /Создать заявку/ }).click()
  await page.getByText('ПОДТВЕРЖДЁННАЯ КАРТОЧКА').waitFor()
  if (await page.getByRole('combobox', { name: 'Услуга' }).count()) {
    await page.getByRole('combobox', { name: 'Услуга' }).selectOption({ index: 1 })
  }
  if (await page.getByRole('combobox', { name: 'Подключение' }).count()) {
    const connection = page.getByRole('combobox', { name: 'Подключение' })
    const eligible = connection.locator('option:not([disabled]):not([value=""])').first()
    await connection.selectOption(await eligible.getAttribute('value'))
  }
  const subject = `Синтетическая проверка Subscriber 360 ${Date.now()}`
  await page.locator('#ticket-subject').fill(subject)
  await page.locator('#ticket-description').fill('Синтетическая проверка создания связанной заявки в dev-окружении.')
  const ticket = await postResponse(page, '/api/v1/tickets', 'Создать заявку', 201)
  if (ticket.ticket_type !== 'SUBSCRIBER_LINKED' || ticket.subscriber_snapshot_id !== card.lookup_id) throw new Error('Linked ticket did not retain confirmed lookup')
  await page.getByRole('heading', { name: subject }).waitFor()
  console.log('NetUP Lab: linked ticket created through Subscriber 360')
  return ticket
}
async function processTicket(page, ticket, engineerId) {
  await page.goto(`${origin}/tickets/${ticket.id}`)
  await page.getByRole('heading', { name: ticket.subject }).waitFor()
  await page.getByRole('combobox', { name: 'Активный инженер' }).selectOption(engineerId)
  await postResponse(page, `/tickets/${ticket.id}/assignment`, 'Назначить')
  const stale = await page.context().request.post(`${origin}/api/v1/tickets/${ticket.id}/start`, {
    headers: { Origin: origin, 'X-CSRF-Token': await csrfToken(page.context()), 'If-Match': '"1"' },
  })
  expectStatus(stale, 409, 'If-Match conflict')
  await postResponse(page, `/tickets/${ticket.id}/start`, /Взять в работу/)
  await page.getByRole('textbox', { name: 'Наблюдение инженера' }).fill('Синтетическая ручная диагностика.')
  await postResponse(page, `/tickets/${ticket.id}/manual-diagnostics`, 'Записать диагностику', 201)
  await page.getByRole('textbox', { name: 'Итоговое решение' }).fill('Синтетическое решение инженера.')
  await page.getByRole('textbox', { name: 'Выполненные действия' }).fill('Синтетические выполненные действия.')
  await postResponse(page, `/tickets/${ticket.id}/decisions`, 'Записать решение', 201)
  await page.getByRole('combobox', { name: 'Решение для закрытия' }).selectOption({ index: 1 })
  await postResponse(page, `/tickets/${ticket.id}/close`, 'Закрыть заявку')
  await page.getByText(/Заявка закрыта инженером/).waitFor()
  const saved = await page.request.get(`${origin}/api/v1/tickets/${ticket.id}`)
  expectStatus(saved, 200, 'Ticket reload')
  if ((await saved.json()).status !== 'CLOSED') throw new Error('Closed status was not persisted')
  const history = await page.request.get(`${origin}/api/v1/tickets/${ticket.id}/history?page=1&size=20`)
  expectStatus(history, 200, 'Ticket history reload')
  if ((await history.json()).total < 6) throw new Error('Ticket workflow history is incomplete')
  console.log(`${ticket.ticket_type}: create, assign, start, diagnostic, decision, close passed`)
}
async function main() {
  const browser = await chromium.launch({ headless: true })
  try {
    const operator = await login(browser, 'dev-operator', 'operator')
    if (process.env.OSS_SCREENSHOTS && billingMode === 'MOCK') await operator.page.screenshot({ path: '/run/screenshots/dashboard.png', fullPage: true })
    expectStatus(await operator.context.request.post(`${origin}/api/v1/tickets`, { data: {} }), 403, 'CSRF rejection')
    expectStatus(await operator.context.request.post(`${origin}/api/v1/tickets`, {
      headers: { Origin: 'https://untrusted.invalid', 'X-CSRF-Token': await csrfToken(operator.context) }, data: {},
    }), 403, 'Origin rejection')
    if (process.env.OSS_READ_ONLY === '1') {
      if (billingMode !== 'NETUP') throw new Error('Read-only Subscriber 360 check requires NetUP Lab')
      await checkNetupLabLookup(operator.page)
      const existingTicket = operator.page.locator('.subscriber-table a[href^="/tickets/"]').first()
      await existingTicket.waitFor()
      await existingTicket.click()
      await operator.page.locator('.subscriber-summary').waitFor()
      for (const phrase of ['Технология: UNKNOWN', 'ONU: UNKNOWN', 'Техническое состояние: UNKNOWN']) {
        if ((await operator.page.locator('.subscriber-summary').textContent()).includes(phrase)) throw new Error('Ticket displays an unconfirmed technical value')
      }
      await operator.page.getByRole('button', { name: /Открыть карточку абонента/ }).click()
      await operator.page.locator('.subscriber-heading h1').waitFor()
      if (!operator.page.url().includes('/subscribers/')) throw new Error('Ticket did not open subscriber card')
      await operator.context.close()
      console.log('NetUP Lab: existing linked ticket and subscriber navigation passed without ticket creation')
      return
    }
    const tickets = []
    const kinds = billingMode === 'NETUP' ? ['MANUAL', 'INTERNAL'] : ['MANUAL', 'INTERNAL', 'SUBSCRIBER_LINKED']
    for (const kind of kinds) tickets.push(await createTicket(operator.page, kind))
    if (billingMode === 'NETUP') {
      const linked = await checkNetupLabLookup(operator.page)
      if (linked) tickets.push(linked)
    }
    await operator.page.goto(`${origin}/tickets/${tickets[0].id}`)
    await operator.page.getByRole('textbox', { name: 'Новый комментарий' }).fill('Синтетическое уточнение оператора.')
    await postResponse(operator.page, `/tickets/${tickets[0].id}/comments`, 'Сохранить комментарий', 201)
    expectStatus(await operator.context.request.post(`${origin}/api/v1/tickets/${tickets[0].id}/start`, {
      headers: { Origin: origin, 'X-CSRF-Token': await csrfToken(operator.context), 'If-Match': '"1"' },
    }), 403, 'Operator cannot start work')
    await operator.page.locator('.fact-item').getByText('Синтетическое уточнение оператора.').waitFor()
    if (process.env.OSS_SCREENSHOTS && billingMode === 'MOCK') await operator.page.screenshot({ path: '/run/screenshots/ticket-detail.png', fullPage: true })
    if (await operator.page.getByRole('button', { name: /Взять в работу/ }).count()) throw new Error('Operator sees engineer action')
    await operator.context.close()
    console.log(`operator: ${billingMode === 'NETUP' ? 'two synthetic ticket types and NetUP lookup' : 'three ticket types and ambiguous selection'}, comment and RBAC passed`)

    const engineer = await login(browser, 'dev-engineer', 'engineer')
    if (billingMode === 'NETUP') {
      const smokeAccount = process.env.OSS_LAB_ACCOUNT_NO
      const financeProbe = await engineer.context.request.post(`${origin}/api/v1/subscriber-lookups`, {
        headers: { Origin: origin, 'X-CSRF-Token': await csrfToken(engineer.context) },
        data: { account_no: smokeAccount },
      })
      expectStatus(financeProbe, 200, 'Engineer Subscriber 360 lookup')
      if ((await financeProbe.json()).financial_accounts.length !== 0) throw new Error('Engineer received financial data')
      console.log('engineer: financial account details withheld')
    }
    const identity = await (await engineer.context.request.get(`${origin}/api/v1/auth/me`)).json()
    for (const ticket of tickets) await processTicket(engineer.page, ticket, identity.user.id)
    await engineer.context.close()

    const admin = await login(browser, 'dev-admin', 'admin')
    if (await admin.page.getByRole('link', { name: 'Новая заявка' }).count()) throw new Error('Admin sees ServiceDesk')
    if (await admin.page.getByRole('link', { name: 'Абоненты' }).count()) throw new Error('Admin sees Subscriber 360')
    await admin.context.close()
    console.log('admin: ServiceDesk access denied')

    const bezhan = await login(browser, 'bezhan', 'engineer')
    const user = await (await bezhan.context.request.get(`${origin}/api/v1/auth/me`)).json()
    if (user.user.role !== 'engineer' || user.permissions.includes('identity.manage')) throw new Error('Engineer permissions mismatch')
    await bezhan.page.getByRole('button', { name: 'Выйти' }).click()
    await bezhan.page.getByRole('heading', { name: 'Вход в систему' }).waitFor()
    await bezhan.page.locator('#username').fill('bezhan')
    await bezhan.page.locator('#current-password').fill(users.bezhan)
    await bezhan.page.getByRole('button', { name: 'Войти' }).click()
    await bezhan.page.getByRole('heading', { name: /Добрый день/ }).waitFor()
    await bezhan.context.close()
    console.log('bezhan: engineer login, logout and repeat login passed')
  } finally { await browser.close() }
}
main().catch((error) => { console.error(`Browser acceptance failed: ${error.message.split('\n')[0]}`); process.exitCode = 1 })
