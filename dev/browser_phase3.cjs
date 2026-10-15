/* Phase 3 Chromium acceptance against a disposable oss_test_* PostgreSQL database. */
const fs = require('node:fs')
const crypto = require('node:crypto')
const { chromium } = require('playwright')

const origin = 'https://172.17.7.2:8443'
const adminPassword = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))['dev-admin']
const staffLogin = `phase3-${Date.now()}`
const nextPassword = `${crypto.randomBytes(24).toString('base64url')}Aa1!`

function status(response, expected, label) {
  if (response.status() !== expected) throw new Error(`${label}: HTTP ${response.status()}`)
}

async function login(page, username, password) {
  await page.goto(origin, { waitUntil: 'networkidle' })
  await page.getByRole('heading', { name: 'Вход в систему' }).waitFor()
  await page.locator('#username').fill(username)
  await page.locator('#current-password').fill(password)
  await page.getByRole('button', { name: 'Войти' }).click()
}

async function csrf(context) {
  const cookie = (await context.cookies(origin)).find((item) => item.name === 'oss_csrf')
  if (!cookie) throw new Error('CSRF cookie missing')
  return cookie.value
}

async function createTicket(page, subject) {
  await page.goto(`${origin}/tickets/new`)
  await page.getByRole('heading', { name: 'Новая заявка' }).waitFor()
  await page.locator('#ticket-subject').fill(subject)
  await page.locator('#ticket-description').fill('Синтетическая запись из изолированной тестовой базы.')
  const pending = page.waitForResponse((response) => response.url().endsWith('/api/v1/tickets') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Создать заявку' }).click()
  const response = await pending
  status(response, 201, 'Create ticket')
  const ticket = await response.json()
  await page.getByRole('heading', { name: subject }).waitFor()
  return ticket
}

async function main() {
  const browser = await chromium.launch({ headless: true })
  try {
    const admin = await browser.newContext({ ignoreHTTPSErrors: false })
    const adminPage = await admin.newPage()
    await login(adminPage, 'dev-admin', adminPassword)
    await adminPage.getByRole('heading', { name: /Добрый день/ }).waitFor()
    const adminMe = await (await admin.request.get(`${origin}/api/v1/auth/me`)).json()
    if (!adminMe.permissions.includes('users.manage') || !adminMe.permissions.includes('tickets.edit.all')) throw new Error('Admin grants missing')
    const foreign = await createTicket(adminPage, `Чужая заявка ${Date.now()}`)

    await adminPage.goto(`${origin}/users`)
    await adminPage.getByRole('heading', { name: 'Пользователи' }).waitFor()
    await adminPage.getByRole('button', { name: 'Создать пользователя' }).click()
    await adminPage.getByRole('textbox', { name: 'Логин' }).fill(staffLogin)
    await adminPage.getByRole('textbox', { name: 'Имя сотрудника' }).fill('Сотрудник теста')
    await adminPage.getByRole('checkbox', { name: 'Просмотр всех' }).uncheck()
    await adminPage.getByRole('checkbox', { name: 'Просмотр своих' }).check()
    const createdPending = adminPage.waitForResponse((response) => response.url().endsWith('/api/v1/users') && response.request().method() === 'POST')
    await adminPage.getByRole('button', { name: 'Создать', exact: true }).click()
    const createdResponse = await createdPending
    status(createdResponse, 201, 'Create employee')
    const created = await createdResponse.json()
    if (created.password_hash || created.password || !created.temporary_password || !created.must_change_password) throw new Error('One-time password response contract failed')
    const temporaryPassword = await adminPage.locator('.temporary-password').textContent()
    if (temporaryPassword !== created.temporary_password) throw new Error('One-time password was not shown in UI')
    console.log('Admin UI: employee creation and one-time password passed')

    const staff = await browser.newContext({ ignoreHTTPSErrors: false })
    const staffPage = await staff.newPage()
    await login(staffPage, staffLogin, temporaryPassword)
    await staffPage.getByRole('heading', { name: 'Смена временного пароля' }).waitFor()
    const prechange = await staff.request.get(`${origin}/api/v1/tickets`)
    status(prechange, 403, 'First-login gate')
    await staffPage.locator('input[name="current-password"]').fill(temporaryPassword)
    await staffPage.locator('input[name="new-password"]').fill(nextPassword)
    await staffPage.locator('input[name="repeat-password"]').fill(nextPassword)
    const changedPending = staffPage.waitForResponse((response) => response.url().endsWith('/api/v1/auth/password') && response.request().method() === 'POST')
    await staffPage.getByRole('button', { name: 'Сменить пароль' }).click()
    status(await changedPending, 200, 'Change temporary password')
    await staffPage.getByRole('heading', { name: 'Вход в систему' }).waitFor()
    await login(staffPage, staffLogin, nextPassword)
    await staffPage.getByRole('heading', { name: /Добрый день/ }).waitFor()
    const staffMe = await (await staff.request.get(`${origin}/api/v1/auth/me`)).json()
    if (!staffMe.permissions.includes('tickets.edit.own') || staffMe.permissions.includes('users.manage') || staffMe.permissions.includes('tickets.read.all')) throw new Error('Employee grants incorrect')
    console.log('Employee UI: mandatory password change and restricted login passed')

    const own = await createTicket(staffPage, `Своя заявка ${Date.now()}`)
    await staffPage.getByRole('button', { name: 'Редактировать заявку' }).click()
    const editedSubject = `${own.subject} — изменена`
    await staffPage.getByRole('textbox', { name: 'Тема' }).fill(editedSubject)
    const editedPending = staffPage.waitForResponse((response) => response.url().endsWith(`/api/v1/tickets/${own.id}`) && response.request().method() === 'PATCH')
    await staffPage.getByRole('button', { name: 'Сохранить изменения' }).click()
    status(await editedPending, 200, 'Edit own ticket')
    await staffPage.getByRole('heading', { name: editedSubject }).waitFor()
    const history = await (await staff.request.get(`${origin}/api/v1/tickets/${own.id}/history`)).json()
    if (!history.items.some((event) => event.event_type === 'ticket_edited' && event.event_data.changes.subject.before === own.subject && event.event_data.changes.subject.after === editedSubject)) throw new Error('Ticket edit history incomplete')
    const denied = await staff.request.patch(`${origin}/api/v1/tickets/${foreign.id}`, {
      headers: { Origin: origin, 'X-CSRF-Token': await csrf(staff), 'If-Match': String(foreign.version) },
      data: { subject: 'Попытка изменения чужой заявки' },
    })
    if (![403, 404].includes(denied.status())) throw new Error(`Foreign ticket edit: HTTP ${denied.status()}`)
    status(await staff.request.post(`${origin}/api/v1/users`, { data: {} }), 403, 'Employee admin denial')
    console.log('Employee UI/API: create, own edit, history, foreign denial passed')

    await adminPage.goto(`${origin}/users`)
    await adminPage.getByRole('heading', { name: 'Пользователи' }).waitFor()
    await adminPage.getByRole('button', { name: 'Сотрудник теста' }).click()
    await adminPage.getByRole('checkbox', { name: 'Просмотр всех' }).check()
    const grantsPending = adminPage.waitForResponse((response) => response.url().endsWith(`/api/v1/users/${created.id}/permissions`) && response.request().method() === 'PUT')
    await adminPage.getByRole('button', { name: 'Сохранить разрешения' }).click()
    status(await grantsPending, 200, 'Grant tickets.read.all')
    const afterGrant = await (await staff.request.get(`${origin}/api/v1/auth/me`)).json()
    if (!afterGrant.permissions.includes('tickets.read.all')) throw new Error('Grant did not reach existing session')
    status(await staff.request.get(`${origin}/api/v1/tickets/${foreign.id}`), 200, 'Existing session new grant')
    await adminPage.getByRole('checkbox', { name: 'Просмотр всех' }).uncheck()
    const revokePending = adminPage.waitForResponse((response) => response.url().endsWith(`/api/v1/users/${created.id}/permissions`) && response.request().method() === 'PUT')
    await adminPage.getByRole('button', { name: 'Сохранить разрешения' }).click()
    status(await revokePending, 200, 'Revoke tickets.read.all')
    status(await staff.request.get(`${origin}/api/v1/tickets/${foreign.id}`), 404, 'Immediate permission revocation')
    const disablePending = adminPage.waitForResponse((response) => response.url().endsWith(`/api/v1/users/${created.id}/deactivate`) && response.request().method() === 'POST')
    await adminPage.getByRole('button', { name: 'Отключить' }).click()
    status(await disablePending, 200, 'Deactivate employee')
    status(await staff.request.get(`${origin}/api/v1/auth/me`), 401, 'Deactivated session')
    console.log('Admin UI/API: live grant, revoke and deactivate passed')
    await staff.close()
    await admin.close()
  } finally {
    await browser.close()
  }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1 })
