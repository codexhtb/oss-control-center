/* Read-only Chromium check of the persistent dev ticket registry. */
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
    const response = await context.request.get(`${origin}/api/v1/tickets?page=1&size=20`)
    if (response.status() !== 200) throw new Error(`Ticket list HTTP ${response.status()}`)
    const data = await response.json()
    const linked = data.items.find((item) => item.ticket_type === 'SUBSCRIBER_LINKED' && item.subscriber_name && item.account_no)
    const manual = data.items.find((item) => item.ticket_type === 'MANUAL')
    if (!linked || !manual) throw new Error('First page lacks linked or manual ticket for browser check')

    for (const [width, height] of [[1366, 768], [1440, 900], [1920, 1080], [390, 844]]) {
      await page.setViewportSize({ width, height })
      await page.goto(`${origin}/tickets`)
      await page.getByRole('heading', { name: 'Заявки', exact: true }).waitFor()
      const linkedRow = page.locator(`tr:has(a[href="/tickets/${linked.id}"])`)
      await linkedRow.waitFor()
      const identity = await linkedRow.locator('.desk-subscriber-cell').textContent()
      if (!identity.includes(linked.subscriber_name) || !identity.includes(`ЛС ${linked.account_no}`)) {
        throw new Error(`Linked identity missing at ${width}x${height}`)
      }
      const manualRow = page.locator(`tr:has(a[href="/tickets/${manual.id}"])`)
      await manualRow.waitFor()
      if ((await manualRow.locator('.desk-subscriber-cell').textContent()).includes('ЛС ')) {
        throw new Error('Manual ticket displays a fabricated account')
      }
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)
      if (overflow) throw new Error(`Horizontal page overflow at ${width}x${height}`)
      if (width === 390) {
        const bounds = await linkedRow.locator('.desk-subscriber-cell').boundingBox()
        if (!bounds || bounds.x < 0 || bounds.x + bounds.width > width + 1) {
          throw new Error('Subscriber identity is outside the mobile viewport')
        }
      }
    }
    await page.locator(`tr:has(a[href="/tickets/${linked.id}"]) .ticket-link`).click()
    await page.getByRole('heading', { name: `Заявка #${linked.number}` }).waitFor()
    if (errors || mutations) throw new Error(`Browser errors: ${errors}; attempted ticket mutations: ${mutations}`)
    console.log('Read-only ticket list: identity, account, manual ticket, navigation and four viewports passed')
    await context.close()
  } finally {
    await browser.close()
  }
}

main().catch(() => { console.error('Read-only ticket list browser check failed'); process.exitCode = 1 })
