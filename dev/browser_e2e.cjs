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
  expectStatus(await context.request.get(`${origin}/api/v1/tickets`), 200, `${role} RBAC`)
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
async function checkNetupLabLookup(page, expectFinance = true, returnCard = false) {
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
  if ((await page.getByRole('heading', { name: 'Финансовые сведения' }).count() === 1) !== expectFinance) throw new Error('Subscriber finance RBAC mismatch')
  if (card.connections.length && !(await page.locator('.ip-group').count())) throw new Error('IP group rows not rendered')
  expectStatus(await page.context().request.get(`${origin}/api/v1/subscriber-lookups/${card.lookup_id}/tickets`), 200, 'Subscriber ticket history')
  console.log('NetUP Lab: full Subscriber 360 card and ticket history passed')
  if (process.env.OSS_STAGE3C_LINKED !== '1') return returnCard ? card : null
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
  await page.getByText('Назначить инженера', { exact: true }).click()
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
  await postResponse(page, `/tickets/${ticket.id}/decisions`, 'Сохранить решение', 201)
  await page.getByText('Закрытие заявки', { exact: true }).click()
  await page.getByRole('combobox', { name: 'Решение для закрытия' }).selectOption({ index: 1 })
  await postResponse(page, `/tickets/${ticket.id}/close`, 'Закрыть заявку')
  await page.getByRole('complementary', { name: 'Управление заявкой' }).getByText(/Закрыта .*История доступна/).waitFor()
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
      if (process.env.OSS_MONITORING_ONLY !== '1') {
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
      }
      const operatorLookup = new URL(operator.page.url()).pathname.split('/').pop()
      const operatorConnection = (await (await operator.context.request.get(`${origin}/api/v1/subscriber-lookups/${operatorLookup}`)).json()).connections[0]
      if (operatorConnection) {
        expectStatus(await operator.context.request.get(`${origin}/api/v1/subscriber-lookups/${operatorLookup}/connections/${operatorConnection.connection_snapshot_id}/onu`), 403, 'Operator ONU RBAC')
        const denied = await operator.context.request.post(`${origin}/api/v1/subscriber-lookups/${operatorLookup}/diagnostics/check`, {
          headers: { Origin: origin, 'X-CSRF-Token': await csrfToken(operator.context) },
          data: { connection_snapshot_id: operatorConnection.connection_snapshot_id },
        })
        expectStatus(denied, 403, 'Operator monitoring RBAC')
      }
      await operator.context.close()
      const engineer = await login(browser, 'dev-engineer', 'engineer')
      const card = await checkNetupLabLookup(engineer.page, false, true)
      if (!card.connections.length) throw new Error('Read-only monitoring browser check needs a confirmed billing connection')
      const onuResponse = await engineer.context.request.get(`${origin}/api/v1/subscriber-lookups/${card.lookup_id}/connections/${card.connections[0].connection_snapshot_id}/onu`)
      expectStatus(onuResponse, 200, 'Subscriber ONU source availability')
      const onu = await onuResponse.json()
      if (onu.source !== 'BDCOM' || !['UNCONFIRMED', 'MATCHED_BY_RULE'].includes(onu.association)) throw new Error('ONU source contract mismatch')
      if (onu.association === 'MATCHED_BY_RULE' && (onu.candidates.length !== 1 || !onu.match)) throw new Error('ONU match lacks unique evidence')
      if (process.env.OSS_ONU_MATCH_ONLY === '1') {
        if (onu.association !== 'MATCHED_BY_RULE' || onu.candidates[0].pon_type !== process.env.OSS_EXPECT_PON_TYPE) throw new Error('Expected unique PON ONU rule match was not returned')
        if (onu.match.billing_source !== 'NETUP' || onu.match.onu_source !== 'LIBRENMS_BDCOM') throw new Error('ONU match provenance missing')
        await engineer.page.locator('.onu-candidate').first().waitFor()
        if (!(await engineer.page.locator('.onu-candidates').textContent()).includes('ONU найдена по утверждённому соответствию OLT')) throw new Error('Browser did not show verified ONU association')
        if (onu.candidates[0].pon_type === 'GPON' && onu.candidates[0].mac) throw new Error('GPON surrogate key exposed as MAC')
        console.log(`Read-only ${onu.candidates[0].pon_type} ONU browser match passed`)
        await engineer.context.close()
        return
      }
      if (process.env.OSS_BDCOM_EXPECT_UNAVAILABLE === '1') {
        if (onu.availability !== 'UNAVAILABLE' || onu.candidates.length) throw new Error('Unconfigured ONU source produced subscriber data')
        await engineer.page.getByText('Источник BDCOM ONU не подключён или недоступен.').first().waitFor()
      } else if (process.env.OSS_BDCOM_EXPECT_CONNECTED === '1') {
        if (onu.availability === 'UNAVAILABLE') throw new Error('Connected BDCOM source is unavailable')
        if (onu.candidates.length) {
          await engineer.page.locator('.onu-candidate').first().waitFor()
          const rendered = await engineer.page.locator('.onu-candidate').count()
          if (rendered !== onu.candidates.length) throw new Error('Browser ONU candidate count differs from API')
          if (onu.candidates.some((item) => item.pon_type === 'GPON' && item.mac)) throw new Error('GPON surrogate key exposed as MAC')
          for (let index = 0; index < onu.candidates.length; index += 1) {
            const item = onu.candidates[index]
            const text = await engineer.page.locator('.onu-candidate').nth(index).textContent()
            const expected = [item.interface, item.olt, item.mac, item.serial,
              ['OFFLINE', 'DEREGISTERED'].includes(item.state) && item.reason !== 'unknown' ? item.reason : null,
              item.onu_rx_dbm, item.onu_tx_dbm, item.olt_rx_dbm,
              ...[item.inventory_polled_at, item.optical_measured_at].filter(Boolean).map((value) => value.replace(/^(\d{4})-(\d{2})-(\d{2})[ T]/, '$3.$2.$1 '))].filter(Boolean)
            if (expected.some((value) => !text.includes(value))) throw new Error('Browser ONU field differs from source response')
            if (!text.includes(item.state === 'UNKNOWN' ? 'UNKNOWN' : item.state[0] + item.state.slice(1).toLowerCase())) throw new Error('Browser ONU state differs from source response')
          }
        }
        console.log(`BDCOM ONU source connected: ${onu.availability}, association ${onu.association}, ${onu.candidates.length} candidates`)
      }
      const pending = engineer.page.waitForResponse((response) => response.url().endsWith(`/subscriber-lookups/${card.lookup_id}/diagnostics/check`) && response.request().method() === 'POST')
      await engineer.page.getByRole('button', { name: 'Проверить подключение' }).first().click()
      const checked = await pending
      expectStatus(checked, 200, 'Subscriber monitoring preview')
      const report = await checked.json()
      if (report.connection_snapshot_id !== card.connections[0].connection_snapshot_id || !Array.isArray(report.observations) || !Array.isArray(report.limitations)) throw new Error('Monitoring preview contract mismatch')
      if (process.env.OSS_MONITORING_ONLY === '1') {
        if (!report.observations.some((item) => item.source === 'LIBRENMS' && item.measured_object.startsWith('device:') && ['UP', 'DOWN'].includes(item.result))) throw new Error('Approved exact-name mapping did not produce a real LibreNMS device state')
        if (!report.limitations.includes('device_mapping_unique_exact_sysname') || report.interface_match !== 'UNKNOWN') throw new Error('Monitoring report hid mapping provenance or inferred an interface')
      }
      await engineer.page.locator('.monitoring-report').first().waitFor()
      const open = await engineer.context.request.get(`${origin}/api/v1/tickets?ticket_type=SUBSCRIBER_LINKED&status=OPEN&size=50`)
      expectStatus(open, 200, 'Open linked ticket list')
      let ticketChecked = false
      for (const candidate of (await open.json()).items) {
        const detailResponse = await engineer.context.request.get(`${origin}/api/v1/tickets/${candidate.id}`)
        expectStatus(detailResponse, 200, 'Open linked ticket detail')
        const detail = await detailResponse.json()
        if (!detail.connection_snapshot_id) continue
        const path = `${origin}/api/v1/tickets/${detail.id}/diagnostics/check`
        expectStatus(await engineer.context.request.post(path, { headers: { Origin: origin, 'If-Match': `"${detail.version}"` } }), 403, 'Monitoring CSRF')
        expectStatus(await engineer.context.request.post(path, { headers: { Origin: 'https://untrusted.invalid', 'X-CSRF-Token': await csrfToken(engineer.context), 'If-Match': `"${detail.version}"` } }), 403, 'Monitoring Origin')
        await engineer.page.goto(`${origin}/tickets/${detail.id}`)
        const ticketOnu = await engineer.context.request.get(`${origin}/api/v1/tickets/${detail.id}/onu`)
        expectStatus(ticketOnu, 200, 'Ticket ONU source availability')
        if (process.env.OSS_BDCOM_EXPECT_UNAVAILABLE === '1') {
          if ((await ticketOnu.json()).availability !== 'UNAVAILABLE') throw new Error('Unconfigured ticket ONU source produced data')
          await engineer.page.getByText('Источник BDCOM ONU не подключён или недоступен.').first().waitFor()
        }
        await engineer.page.getByRole('heading', { name: 'Диагностика подключения' }).waitFor()
        const oltCheck = engineer.page.getByText('Проверка OLT в мониторинге', { exact: true })
        if (await oltCheck.count()) await oltCheck.click()
        const ticketPending = engineer.page.waitForResponse((response) => response.url().endsWith(`/tickets/${detail.id}/diagnostics/check`) && response.request().method() === 'POST')
        await engineer.page.getByRole('button', { name: /^Проверить (подключение|OLT)$/ }).click()
        expectStatus(await ticketPending, 200, 'Ticket monitoring preview')
        await engineer.page.locator('.monitoring-report').first().waitFor()
        ticketChecked = true
        break
      }
      if (!ticketChecked) throw new Error('No existing open linked ticket with a confirmed connection for browser monitoring check')
      await engineer.context.close()
      console.log('NetUP Lab: Subscriber 360 and ticket monitoring preview, CSRF, Origin and RBAC passed without ticket creation')
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
    if (!(await admin.page.getByRole('link', { name: 'Новая заявка' }).count())) throw new Error('Admin lacks ServiceDesk')
    if (!(await admin.page.getByRole('link', { name: 'Абоненты' }).count())) throw new Error('Admin lacks Subscriber 360')
    if (!(await admin.page.getByRole('link', { name: 'Пользователи' }).count())) throw new Error('Admin lacks user administration')
    await admin.context.close()
    console.log('admin: individual full-access grants passed')

    const bezhan = await login(browser, 'bezhan', 'engineer')
    const user = await (await bezhan.context.request.get(`${origin}/api/v1/auth/me`)).json()
    if (user.user.role !== 'engineer' || user.permissions.includes('users.manage')) throw new Error('Engineer permissions mismatch')
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
