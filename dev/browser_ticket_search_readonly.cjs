/* Read-only Chromium acceptance of the persistent dev ticket search. */
const fs = require('node:fs')
const { chromium } = require('playwright')

const origin = 'https://172.17.7.2:8443'

async function main() {
  const users = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))
  const browser = await chromium.launch({ headless: true })
  try {
    const context = await browser.newContext({ ignoreHTTPSErrors: false })
    const page = await context.newPage()
    let errors = 0
    let mutations = 0
    page.on('pageerror', () => { errors++ })
    await page.route('**/api/v1/tickets**', async (route) => {
      if (route.request().method() !== 'GET') {
        mutations++
        await route.abort()
        return
      }
      await route.continue()
    })
    await page.goto(origin)
    await page.locator('#username').fill('dev-operator')
    await page.locator('#current-password').fill(users['dev-operator'])
    await page.getByRole('button', { name: 'Войти' }).click()
    await page.getByRole('heading', { name: /Добрый день/ }).waitFor()
    const response = await context.request.get(`${origin}/api/v1/tickets?page=1&size=100`)
    if (response.status() !== 200) throw new Error(`Ticket list HTTP ${response.status()}`)
    const data = await response.json()
    const linked = data.items.find((item) => item.ticket_type === 'SUBSCRIBER_LINKED' && item.subscriber_name && item.account_no)
    if (!linked) throw new Error('No linked ticket with historical identity available')

    await page.goto(`${origin}/tickets`)
    await page.getByRole('heading', { name: 'Заявки', exact: true }).waitFor()
    const search = page.getByRole('searchbox', { name: 'Поиск по заявкам' })
    const row = page.locator(`tr:has(a[href="/tickets/${linked.id}"])`)
    await search.fill(linked.subscriber_name)
    await row.waitFor()
    if (!new URL(page.url()).searchParams.has('q')) throw new Error('Search query missing from URL')
    await search.fill(linked.account_no)
    await row.waitFor()
    await page.waitForFunction(() => new URL(location.href).searchParams.get('q') !== null)
    if (new URL(page.url()).searchParams.get('q') !== linked.account_no) {
      await page.waitForURL((url) => url.searchParams.get('q') === linked.account_no)
    }

    const date = new Date(linked.created_at)
    const localDate = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
    await page.getByLabel('Создана от').fill(localDate)
    await page.getByLabel('Создана до').fill(localDate)
    await row.waitFor()
    await page.getByLabel('Статус').selectOption(linked.status)
    await row.waitFor()
    const current = new URL(page.url())
    for (const key of ['q', 'created_from', 'created_before', 'status']) {
      if (!current.searchParams.has(key)) throw new Error(`Filter ${key} missing from URL`)
    }

    await row.locator('.ticket-link').click()
    await page.getByRole('heading', { name: `Заявка #${linked.number}` }).waitFor()
    await page.getByRole('link', { name: 'К реестру заявок' }).click()
    await row.waitFor()
    if (new URL(page.url()).searchParams.get('q') !== linked.account_no) {
      throw new Error('Search filters were not restored after ticket detail')
    }

    for (const [width, height] of [[1366, 768], [1440, 900], [390, 844]]) {
      await page.setViewportSize({ width, height })
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)
      if (overflow) throw new Error(`Page overflow at ${width}x${height}`)
      if (!(await search.isVisible())) throw new Error(`Search hidden at ${width}x${height}`)
    }
    if (errors || mutations) throw new Error(`Browser errors: ${errors}; ticket mutations: ${mutations}`)
    console.log('Read-only ticket search: historical name/account, date/status, return link, responsive layout passed')
    await context.close()
  } finally {
    await browser.close()
  }
}

main().catch(() => { console.error('Read-only ticket search browser check failed'); process.exitCode = 1 })
