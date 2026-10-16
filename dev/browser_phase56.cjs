/* Resolution acceptance uses the existing disposable Phase 5.5 runner/database. */
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
    throw new Error('Refusing browser mutations outside a disposable database')
  }
  const users = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))
  const browser = await chromium.launch({ headless: true })
  try {
    const operator = await login(browser, 'dev-operator', users['dev-operator'])
    await operator.page.goto(`${origin}/tickets/new`)
    await operator.page.getByRole('button', { name: /Ручная/ }).click()
    await operator.page.locator('#ticket-subject').fill('Синтетический вердикт Phase 5.6')
    await operator.page.locator('#ticket-description').fill('Одноразовая база для проверки решения.')
    const created = operator.page.waitForResponse((r) => r.url().endsWith('/api/v1/tickets') && r.request().method() === 'POST')
    await operator.page.getByRole('button', { name: 'Создать заявку' }).click()
    const response = await created
    if (response.status() !== 201) throw new Error(`Create HTTP ${response.status()}`)
    const ticket = await response.json()
    const path = `/api/v1/tickets/${ticket.id}`

    const engineer = await login(browser, 'dev-engineer', users['dev-engineer'])
    await engineer.page.goto(`${origin}/tickets/${ticket.id}`)
    const claimed = engineer.page.waitForResponse((r) => r.url().endsWith(`${path}/claim`) && r.request().method() === 'POST')
    await engineer.page.getByRole('button', { name: 'Взять в работу' }).click()
    if ((await claimed).status() !== 200) throw new Error('Claim failed')
    await engineer.page.getByRole('textbox', { name: 'Решение инженера' }).fill('Синтетический итоговый вердикт')
    await engineer.page.getByRole('textbox', { name: 'Выполненные действия' }).fill('Проверен синтетический канал')
    const resolved = engineer.page.waitForResponse((r) => r.url().endsWith(`${path}/resolve`) && r.request().method() === 'POST')
    await engineer.page.getByRole('button', { name: 'Решено' }).click()
    if ((await resolved).status() !== 200) throw new Error('Resolve failed')
    const panel = engineer.page.getByRole('complementary', { name: 'Управление заявкой' })
    await panel.getByText(/Ожидает подтверждения абонента/).waitFor()
    await engineer.page.getByText('Синтетический итоговый вердикт').waitFor()
    if (await panel.getByRole('button', { name: /Взять в работу|Освободить заявку/ }).count()) {
      throw new Error('Resolved ticket retained active engineer action')
    }

    await engineer.page.goto(`${origin}/tickets?queue=mine`)
    await engineer.page.getByRole('button', { name: /Взяты мной 0/ }).waitFor()
    if (await engineer.page.locator(`a[href="/tickets/${ticket.id}"].ticket-link`).count()) {
      throw new Error('Resolved ticket remained in engineer queue')
    }
    await engineer.page.goto(`${origin}/tickets?status=RESOLVED`)
    await engineer.page.getByRole('combobox', { name: 'Статус' }).waitFor()
    await engineer.page.locator(`a[href="/tickets/${ticket.id}"].ticket-link`).waitFor()
    await engineer.page.getByRole('searchbox', { name: 'Поиск по заявкам' }).fill('Синтетический итоговый вердикт')
    await engineer.page.locator(`a[href="/tickets/${ticket.id}"].ticket-link`).waitFor()

    await operator.page.goto(`${origin}/tickets/${ticket.id}`)
    await operator.page.getByText('Синтетический итоговый вердикт').waitFor()
    if (await operator.page.getByRole('button', { name: 'Решено' }).count()) {
      throw new Error('Operator received engineer resolve action')
    }
    for (const [width, height] of [[1366, 768], [1440, 900], [390, 844]]) {
      await engineer.page.setViewportSize({ width, height })
      if (await engineer.page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) {
        throw new Error(`Resolution UI overflow at ${width}x${height}`)
      }
    }
    console.log('Disposable Chromium: claim, verdict, resolved status, queues, search, RBAC passed')
    await operator.context.close()
    await engineer.context.close()
  } finally {
    await browser.close()
  }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1 })
