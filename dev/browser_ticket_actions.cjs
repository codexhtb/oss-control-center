/* Mutations are permitted only through run_phase3_browser_e2e.sh, on oss_test_*. */
const fs = require('node:fs')
const crypto = require('node:crypto')
const { chromium } = require('playwright')

const origin = 'https://172.17.7.2:8443'
const adminPassword = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))['dev-admin']
if (!/^oss_test_phase3_browser_[0-9]+$/.test(process.env.OSS_DISPOSABLE_DB || '')) {
  throw new Error('Use the disposable PostgreSQL browser runner')
}

function status(response, expected, label) {
  if (response.status() !== expected) throw new Error(`${label}: HTTP ${response.status()}`)
}

async function headers(context) {
  const csrf = (await context.cookies(origin)).find((item) => item.name === 'oss_csrf')
  if (!csrf) throw new Error('CSRF cookie missing')
  return { Origin: origin, 'X-CSRF-Token': csrf.value }
}

async function login(page, login, password) {
  await page.goto(origin)
  await page.locator('#username').fill(login)
  await page.locator('#current-password').fill(password)
  await page.getByRole('button', { name: 'Войти' }).click()
}

async function employee(admin, browser, role) {
  const loginName = `panel-${role}-${Date.now()}`
  const response = await admin.request.post(`${origin}/api/v1/users`, {
    headers: await headers(admin), data: { login: loginName, display_name: `Тест панели ${role}`, role },
  })
  status(response, 201, 'Create disposable employee')
  const user = await response.json()
  const password = `${crypto.randomBytes(24).toString('base64url')}Aa1!`
  const context = await browser.newContext({ ignoreHTTPSErrors: false })
  const page = await context.newPage()
  await login(page, loginName, user.temporary_password)
  await page.getByRole('heading', { name: 'Смена временного пароля' }).waitFor()
  status(await context.request.post(`${origin}/api/v1/auth/password`, {
    headers: await headers(context), data: { current_password: user.temporary_password, new_password: password },
  }), 200, 'Set disposable password')
  await login(page, loginName, password)
  await page.getByRole('heading', { name: /Добрый день/ }).waitFor()
  return { context, page, id: user.id }
}

async function createTicket(page, subject) {
  await page.goto(`${origin}/tickets/new`)
  await page.locator('#ticket-subject').fill(subject)
  await page.locator('#ticket-description').fill('Синтетическая запись только во временной БД.')
  const pending = page.waitForResponse((r) => r.url().endsWith('/api/v1/tickets') && r.request().method() === 'POST')
  await page.getByRole('button', { name: 'Создать заявку' }).click()
  const response = await pending
  status(response, 201, 'Create disposable ticket')
  await page.getByRole('heading', { name: subject }).waitFor()
  return response.json()
}

async function mutate(page, ticket, suffix, button, expected = 200) {
  const response = page.waitForResponse((r) => r.url().endsWith(`/api/v1/tickets/${ticket.id}${suffix}`) && r.request().method() === 'POST')
  const refresh = page.waitForResponse((r) => r.url().endsWith(`/api/v1/tickets/${ticket.id}`) && r.request().method() === 'GET')
  await page.getByRole('button', { name: button, exact: true }).click()
  status(await response, expected, suffix)
  status(await refresh, 200, 'Refresh ticket version')
  await page.getByText('Изменение сохранено в заявке.', { exact: true }).waitFor()
}

async function main() {
  const browser = await chromium.launch({ headless: true })
  try {
    const admin = await browser.newContext({ ignoreHTTPSErrors: false })
    const page = await admin.newPage()
    await login(page, 'dev-admin', adminPassword)
    await page.getByRole('heading', { name: /Добрый день/ }).waitFor()
    const engineer = await employee(admin, browser, 'engineer')
    const operator = await employee(admin, browser, 'operator')
    const ticket = await createTicket(page, 'Панель управления — тест workflow')
    const panel = page.getByRole('complementary', { name: 'Управление заявкой' })
    await panel.getByRole('heading', { name: `Заявка #${ticket.number}` }).waitFor()
    if (await panel.getByRole('button', { name: 'Удалить', exact: true }).isVisible()) throw new Error('Delete is not hidden in More')
    if (await panel.getByRole('button', { name: 'Закрыть заявку' }).count()) throw new Error('Close shown for OPEN')
    status(await admin.request.post(`${origin}/api/v1/tickets/${ticket.id}/start`, {
      headers: { Origin: origin, 'If-Match': '"1"' },
    }), 403, 'CSRF enforced')
    await panel.getByText('Назначить инженера', { exact: true }).click()
    await panel.getByRole('combobox', { name: 'Активный инженер' }).selectOption(engineer.id)
    await mutate(page, ticket, '/assignment', 'Назначить')
    status(await admin.request.post(`${origin}/api/v1/tickets/${ticket.id}/start`, {
      headers: { ...await headers(admin), 'If-Match': '"1"' },
    }), 409, 'Stale version rejected')
    await mutate(page, ticket, '/start', 'Взять в работу')
    if (await panel.getByRole('button', { name: 'Взять в работу' }).count()) throw new Error('Start remains after transition')
    if (await panel.getByText('Закрытие заявки', { exact: true }).count()) throw new Error('Close available without a decision')
    await panel.getByText('Выездные работы', { exact: true }).click()
    await panel.getByRole('textbox', { name: 'Причина передачи' }).fill('Синтетическая проверка выездных работ')
    await mutate(page, ticket, '/field-work-request', 'Зафиксировать запрос')
    await panel.getByRole('button', { name: 'Записать решение', exact: true }).click()
    if (!await page.getByRole('textbox', { name: 'Итоговое решение' }).evaluate((node) => node === document.activeElement)) throw new Error('Decision shortcut did not focus the form')
    await page.getByRole('textbox', { name: 'Итоговое решение' }).fill('Синтетическое решение')
    await page.getByRole('textbox', { name: 'Выполненные действия' }).fill('Синтетическая проверка')
    await mutate(page, ticket, '/decisions', 'Сохранить решение', 201)
    await panel.getByText('Закрытие заявки', { exact: true }).click()
    await mutate(page, ticket, '/close', 'Закрыть заявку')
    await panel.getByText('Закрыта', { exact: true }).waitFor()
    if (await panel.getByText('Назначить инженера', { exact: true }).count()) throw new Error('Assignment shown for CLOSED')
    if (await panel.getByRole('button', { name: 'Записать решение' }).count()) throw new Error('Decision action shown for CLOSED')

    const own = await createTicket(operator.page, 'Панель управления — тест прав')
    const operatorPanel = operator.page.getByRole('complementary', { name: 'Управление заявкой' })
    if (await operatorPanel.getByRole('button').count()) throw new Error('Default operator has workflow buttons')
    status(await operator.context.request.post(`${origin}/api/v1/tickets/${own.id}/start`, {
      headers: { ...await headers(operator.context), 'If-Match': '"1"' },
    }), 403, 'Operator workflow denied')
    await engineer.page.goto(`${origin}/tickets/${own.id}`)
    const engineerPanel = engineer.page.getByRole('complementary', { name: 'Управление заявкой' })
    await engineerPanel.getByRole('button', { name: 'Взять в работу' }).waitFor()
    if (await engineerPanel.getByText('Дополнительно', { exact: true }).count()) throw new Error('Engineer gets deletion by default')
    status(await engineer.context.request.delete(`${origin}/api/v1/tickets/${own.id}`, {
      headers: { ...await headers(engineer.context), 'If-Match': '"1"' }, data: {},
    }), 403, 'Engineer deletion denied')

    await page.goto(`${origin}/tickets/${own.id}`)
    await panel.getByText('Дополнительно', { exact: true }).click()
    await panel.getByRole('button', { name: 'Удалить', exact: true }).click()
    await panel.getByRole('group', { name: 'Подтверждение удаления' }).waitFor()
    const deleted = page.waitForResponse((r) => r.url().endsWith(`/api/v1/tickets/${own.id}`) && r.request().method() === 'DELETE')
    await panel.getByRole('button', { name: 'Подтвердить удаление' }).click()
    status(await deleted, 200, 'Confirmed soft delete')
    status(await admin.request.get(`${origin}/api/v1/tickets/${own.id}`), 404, 'Soft-deleted ticket hidden')
    console.log('Ticket panel Chromium: assignment, start, decision focus/save, close, operator/engineer RBAC, confirmed soft delete passed on disposable database')
    await engineer.context.close()
    await operator.context.close()
    await admin.close()
  } finally { await browser.close() }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1 })
