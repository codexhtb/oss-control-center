/* Telegram T1 acceptance against a disposable PostgreSQL database. */
const fs = require('node:fs')
const { chromium } = require('playwright')

const origin = 'https://172.17.7.2:8443'

async function main() {
  if (!process.env.OSS_DISPOSABLE_DB?.startsWith('oss_test_phase55_browser_')) {
    throw new Error('Refusing mutations outside disposable database')
  }
  const users = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))
  const browser = await chromium.launch({ headless: true })
  try {
    const context = await browser.newContext({ ignoreHTTPSErrors: false })
    const page = await context.newPage()
    await page.goto(origin)
    await page.locator('#username').fill('dev-engineer')
    await page.locator('#current-password').fill(users['dev-engineer'])
    await page.getByRole('button', { name: 'Войти' }).click()
    await page.getByRole('heading', { name: /Добро пожаловать|Добрый день/ }).waitFor()
    await page.goto(`${origin}/tickets`)
    await page.getByText('Синтетическая выездная проверка Telegram T1').click()
    await page.getByText('Выездные работы').click()
    await page.getByRole('textbox', { name: 'Причина передачи' })
      .fill('Синтетическая проверка исходящей передачи Telegram T1')

    let sendsBeforeConfirmation = 0
    page.on('request', (request) => {
      if (request.url().includes('/field-work-handoffs/') && request.url().endsWith('/send')) {
        sendsBeforeConfirmation += 1
      }
    })
    const saved = page.waitForResponse((response) =>
      response.url().endsWith('/field-work-request') && response.request().method() === 'POST')
    await page.getByRole('button', { name: 'Зафиксировать запрос' }).click()
    if ((await saved).status() !== 200) throw new Error('Field work draft was not saved')
    if (sendsBeforeConfirmation !== 0) throw new Error('Saving the reason sent Telegram implicitly')
    const preview = page.locator('.field-dispatch-preview pre')
    await preview.waitFor()
    const previewText = await preview.textContent()
    if (!previewText?.includes('Абонент: Синтетический абонент Telegram T1') ||
        !previewText.includes('Адрес: г. Тест, ул. Безопасная, д. 20')) {
      throw new Error('Telegram preview does not use the immutable subscriber snapshot')
    }

    const delivered = page.waitForResponse((response) =>
      response.url().endsWith('/send') && response.request().method() === 'POST')
    await page.getByRole('button', { name: 'Отправить боту' }).click()
    const deliveredResponse = await delivered
    if (deliveredResponse.status() !== 200) {
      throw new Error(`Telegram send HTTP ${deliveredResponse.status()}`)
    }
    const result = await deliveredResponse.json()
    if (result.delivery_status !== 'SENT' || !result.telegram_message_id) {
      throw new Error('Telegram receipt was not persisted')
    }
    await page.getByText(/Отправлено.*ID сообщения:/).waitFor()
    if (await page.getByRole('button', { name: 'Отправить боту' }).count()) {
      throw new Error('Send action remained available after confirmed delivery')
    }
    console.log('Disposable Chromium: Telegram T1 draft and confirmed LAB delivery passed')
    await context.close()
  } finally {
    await browser.close()
  }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1 })
