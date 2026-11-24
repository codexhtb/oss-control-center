/* Mutations are permitted only in the separate, disposable acceptance stack. */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const { chromium } = require('playwright')
const origin = 'https://sd.oss-ab.net'

async function main() {
  assert.equal(process.env.OSS_DISPOSABLE_DB, 'oss_test_notification_center')
  const users = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))
  const browser = await chromium.launch({ headless: true })
  const contexts = []
  let passed = false
  try {
    async function login(name) {
      const context = await browser.newContext({ viewport: { width: 1280, height: 900 } })
      contexts.push(context)
      const page = await context.newPage()
      await page.goto(origin)
      await page.locator('#username').fill(name)
      await page.locator('#current-password').fill(users[name])
      await page.getByRole('button', { name: 'Войти' }).click()
      await page.getByRole('heading', { name: /Добро пожаловать/ }).waitFor()
      return { context, page }
    }
    async function request(context, path, method = 'GET', data, extraHeaders = {}) {
      const cookies = await context.cookies()
      const csrf = cookies.find((cookie) => cookie.name === 'oss_csrf')?.value
      const result = await context.request.fetch(origin + path, { method, data,
        headers: { Origin: origin, 'X-CSRF-Token': csrf || '', ...extraHeaders } })
      assert.ok(result.ok(), `Acceptance API failed: ${method} ${path} HTTP ${result.status()}`)
      return result.status() === 204 ? null : result.json()
    }
    const engineer = await login('dev-engineer')
    const operator = await login('dev-operator')
    const operatorMe = await request(operator.context, '/api/v1/auth/me')
    const ticket = await request(engineer.context, '/api/v1/tickets', 'POST', {
      ticket_type: 'MANUAL', subject: 'Synthetic notification acceptance', client_request_id: crypto.randomUUID(),
      caller_full_name: 'Synthetic Contact', caller_phone: '+7 900 000 00 00', city: 'Test City',
    })
    const task = await request(engineer.context, '/api/v1/tasks', 'POST', {
      ticket_id: ticket.id, assignee_id: operatorMe.user.id,
      description: 'Synthetic notification task', client_request_id: crypto.randomUUID(),
      reminder_at: new Date(Date.now() + 3600000).toISOString(),
    })
    await operator.page.reload()
    await operator.page.getByRole('button', { name: 'Уведомления, непрочитанных: 1', exact: true }).click()
    await operator.page.getByRole('region', { name: 'Уведомления', exact: true }).getByRole('tab', { name: 'Непрочитанные', exact: true }).waitFor()
    await operator.page.getByRole('button', { name: /Новое поручение/ }).click()
    await operator.page.waitForURL(`**/tickets/${ticket.id}?task=${task.id}`)
    await operator.page.getByRole('button', { name: 'Уведомления, непрочитанных: 0', exact: true }).waitFor()
    // One submit: the answer belongs to COMPLETED and must never create MESSAGE.
    await operator.page.getByRole('textbox', { name: 'Ответ', exact: true }).fill('Synthetic completed answer')
    await operator.page.getByRole('button', { name: 'Завершить поручение', exact: true }).click()
    await operator.page.getByText('Завершено', { exact: true }).first().waitFor()
    await engineer.page.reload()
    await engineer.page.getByRole('button', { name: 'Уведомления, непрочитанных: 1', exact: true }).click()
    assert.equal(await engineer.page.getByRole('button', { name: /Поручение завершено/ }).count(), 1)
    assert.equal(await engineer.page.getByText('Ответ по поручению', { exact: true }).count(), 0)
    if (process.env.OSS_SCREENSHOTS === '1') await engineer.page.screenshot({ path: '/run/screenshots/notification-center-unread.png' })
    await engineer.page.getByRole('button', { name: /Поручение завершено/ }).click()
    await engineer.page.waitForURL(`**/tickets/${ticket.id}?task=${task.id}`)
    await engineer.page.getByRole('button', { name: 'Уведомления, непрочитанных: 0', exact: true }).click()
    await engineer.page.getByText('Непрочитанных уведомлений нет.', { exact: true }).waitFor()
    await engineer.page.getByRole('region', { name: 'Уведомления', exact: true }).getByRole('tab', { name: 'История', exact: true }).click()
    await engineer.page.getByRole('button', { name: /Поручение завершено/ }).waitFor()
    await engineer.page.reload()
    await engineer.page.getByRole('button', { name: 'Уведомления, непрочитанных: 0', exact: true }).click()
    await engineer.page.getByText('Непрочитанных уведомлений нет.', { exact: true }).waitFor()
    // Two future events, no browser mutation of persistent operational data.
    for (let index = 0; index < 2; index++) await request(engineer.context, '/api/v1/tasks', 'POST', {
      ticket_id: ticket.id, assignee_id: operatorMe.user.id,
      description: `Synthetic bulk task ${index}`, client_request_id: crypto.randomUUID(),
    })
    await operator.page.goto(origin)
    await operator.page.getByRole('button', { name: 'Уведомления, непрочитанных: 2', exact: true }).click()
    await operator.page.getByRole('button', { name: 'Прочитать все', exact: true }).click()
    await operator.page.getByText('Непрочитанных уведомлений нет.', { exact: true }).waitFor()
    await operator.page.getByRole('button', { name: 'Уведомления, непрочитанных: 0', exact: true }).waitFor()
    await operator.page.getByRole('region', { name: 'Уведомления', exact: true }).getByRole('tab', { name: 'История', exact: true }).click()
    assert.equal((await request(operator.context, '/api/v1/notifications/center?state=read')).total, 3)
    await operator.page.setViewportSize({ width: 390, height: 844 })
    await operator.page.waitForFunction(() => document.querySelector('.sidebar').getBoundingClientRect().right <= 1)
    const popover = await operator.page.locator('.notification-bell__popover').boundingBox()
    assert.ok(popover && popover.x >= 0 && popover.x + popover.width <= 391)
    if (process.env.OSS_SCREENSHOTS === '1') await operator.page.screenshot({ path: '/run/screenshots/notification-center-mobile.png' })
    const eventPage = await request(engineer.context, `/api/v1/tasks/${task.id}/events`)
    assert.deepEqual(eventPage.items.map((event) => event.kind).sort(), ['COMPLETED', 'CREATED', 'REMINDER'])
    assert.equal((await request(engineer.context, `/api/v1/tickets/${ticket.id}`)).status, ticket.status)
    passed = true
    console.log('PASS: DEV browser completion, navigation, persisted reads, history, bulk read, mobile and independent ticket status')
  } finally {
    if (!passed) for (const context of contexts) for (const page of context.pages()) {
      console.log('Failure page:', page.url())
      console.log('Alerts:', await page.getByRole('alert').allTextContents())
      await page.screenshot({ path: `/run/screenshots/notification-failure-${contexts.indexOf(context)}.png` })
    }
    await Promise.all(contexts.map((context) => context.close()))
    await browser.close()
  }
}
main().catch((error) => { console.error(error.message); process.exitCode = 1 })
