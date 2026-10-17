const fs = require('node:fs')
const { chromium } = require('playwright')

const origin = 'https://172.17.7.2:8443'

async function assertNoHorizontalOverflow(page, viewport) {
  await page.setViewportSize(viewport)
  if (await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)) {
    throw new Error(`Horizontal overflow at ${viewport.width}x${viewport.height}`)
  }
}

async function main() {
  const users = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))
  const browser = await chromium.launch({ headless: true })
  try {
    const desktop = await browser.newContext({ viewport: { width: 1440, height: 900 } })
    const page = await desktop.newPage()
    await page.goto(origin)
    await page.getByRole('heading', { name: 'Оптические системы связи' }).waitFor()
    await page.getByRole('heading', { name: 'Вход в систему' }).waitFor()
    if (await page.getByText('Контроль заявок.').count()) throw new Error('Legacy login copy is still visible')
    if (!await page.locator('.login-hero__module').count()) throw new Error('OSS hero mark is missing')
    await assertNoHorizontalOverflow(page, { width: 1440, height: 900 })

    await page.locator('#username').fill('dev-operator')
    await page.locator('#current-password').fill(users['dev-operator'])
    await page.getByRole('button', { name: 'Войти' }).click()
    await page.getByRole('heading', { name: /Добро пожаловать,/ }).waitFor()
    await page.getByRole('link', { name: /Заявки/ }).click()
    await page.getByRole('heading', { name: 'Заявки' }).waitFor()
    await page.getByRole('link', { name: /Обзор/ }).click()
    await page.getByRole('heading', { name: /Добрый день,/ }).waitFor()
    await page.getByRole('button', { name: 'Выйти' }).click()
    await page.getByRole('heading', { name: 'Вход в систему' }).waitFor()
    await desktop.close()

    const mobile = await browser.newContext({ viewport: { width: 390, height: 844 } })
    const mobilePage = await mobile.newPage()
    await mobilePage.goto(origin)
    await mobilePage.getByRole('heading', { name: 'Оптические системы связи' }).waitFor()
    await mobilePage.getByRole('heading', { name: 'Вход в систему' }).waitFor()
    await assertNoHorizontalOverflow(mobilePage, { width: 390, height: 844 })
    await mobile.close()

    const reduced = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1366, height: 768 } })
    const reducedPage = await reduced.newPage()
    await reducedPage.goto(origin)
    const duration = await reducedPage.locator('.login-hero__mark').evaluate((element) =>
      Number.parseFloat(getComputedStyle(element).animationDuration))
    if (duration > 0.01) throw new Error(`Reduced motion animation duration is ${duration}s`)
    await reduced.close()
    console.log('Chromium: login hero, one-time welcome, mobile layout and reduced motion passed')
  } finally {
    await browser.close()
  }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1 })
