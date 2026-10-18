/* Read-only welcome and motion acceptance against the existing dev UI. */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const { chromium } = require('playwright')

const origin = 'https://172.17.7.2:8443'

async function main() {
  assert.equal(process.env.OSS_READ_ONLY, '1', 'Read-only browser mode is required')
  const users = JSON.parse(fs.readFileSync('/run/dev-users.json', 'utf8'))
  const browser = await chromium.launch({ headless: true })
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } })
    const page = await context.newPage()
    await page.goto(origin)
    await page.locator('#username').fill('dev-operator')
    await page.locator('#current-password').fill(users['dev-operator'])
    await page.getByRole('button', { name: 'Войти' }).click()
    await page.getByRole('heading', { name: /Добро пожаловать/ }).waitFor()

    const name = page.locator('.workspace-welcome__name')
    const operatorNameSize = await name.evaluate((element) => getComputedStyle(element).fontSize)
    assert.ok(parseFloat(operatorNameSize) > parseFloat(await page.locator('.workspace-welcome__greeting').evaluate((element) => getComputedStyle(element).fontSize)))
    assert.equal(await name.evaluate((element) => getComputedStyle(element).borderBottomStyle), 'none')
    assert.equal(await page.locator('.workspace-welcome__name-word').count(), 2)
    assert.ok(await page.locator('.workspace-welcome__name-letter').count() > 10)
    assert.equal(await page.locator('.workspace-welcome__name-letter').first().evaluate((element) => getComputedStyle(element).animationName), 'workspace-letter-assemble')
    assert.equal(await page.locator('.workspace-welcome__particle').count(), 0)
    await name.click()
    assert.equal(await page.locator('.workspace-welcome__particle').count(), 10)
    await page.waitForTimeout(350)
    if (process.env.OSS_SCREENSHOTS === '1') {
      await page.screenshot({ path: '/run/screenshots/workspace-welcome-desktop.png' })
    }

    await page.goto(`${origin}/tickets`)
    await page.goto(origin)
    await page.getByRole('heading', { name: /Добро пожаловать/ }).waitFor()
    assert.equal(await page.getByRole('heading', { name: /Добрый день/ }).count(), 0)
    await page.reload()
    await page.getByRole('heading', { name: /Добро пожаловать/ }).waitFor()
    if (process.env.OSS_SCREENSHOTS === '1') {
      await page.screenshot({ path: '/run/screenshots/workspace-name-entrance-early.png' })
      await page.waitForTimeout(420)
      await page.screenshot({ path: '/run/screenshots/workspace-name-entrance-middle.png' })
    }

    await page.emulateMedia({ reducedMotion: 'reduce' })
    assert.equal(await page.locator('.workspace-welcome__name-letter').first().evaluate((element) => getComputedStyle(element).animationName), 'none')
    await page.locator('.workspace-welcome__name').click()
    assert.equal(await page.locator('.workspace-welcome__particle').count(), 10)
    assert.equal(await page.locator('.workspace-welcome__particle').first().evaluate((element) => getComputedStyle(element).display), 'none')
    assert.notEqual(await page.locator('.workspace-welcome__burst').evaluate((element) => getComputedStyle(element, '::after').content), 'none')

    await page.setViewportSize({ width: 390, height: 844 })
    await page.waitForTimeout(1700)
    const dimensions = await page.evaluate(() => ({ width: innerWidth, scrollWidth: document.documentElement.scrollWidth }))
    assert.ok(dimensions.scrollWidth <= dimensions.width + 1, `Mobile layout overflows: ${JSON.stringify(dimensions)}`)
    if (process.env.OSS_SCREENSHOTS === '1') {
      await page.screenshot({ path: '/run/screenshots/workspace-welcome-mobile.png' })
    }
    console.log('Welcome desktop, return visit, click effect, reduced motion and mobile layout passed')
    await context.close()

    for (const login of ['dev-engineer', 'dev-admin']) {
      const otherContext = await browser.newContext({ viewport: { width: 1280, height: 800 } })
      try {
        const otherPage = await otherContext.newPage()
        await otherPage.goto(origin)
        await otherPage.locator('#username').fill(login)
        await otherPage.locator('#current-password').fill(users[login])
        await otherPage.getByRole('button', { name: 'Войти' }).click()
        await otherPage.getByRole('heading', { name: /Добро пожаловать/ }).waitFor()
        assert.equal(await otherPage.locator('.workspace-welcome').count(), 1)
        assert.equal(await otherPage.locator('.workspace-welcome__name').evaluate((element) => getComputedStyle(element).fontSize), operatorNameSize)
        assert.equal(await otherPage.locator('.workspace-welcome__name-letter').first().evaluate((element) => getComputedStyle(element).animationName), 'workspace-letter-assemble')
        assert.equal(await otherPage.locator('.workspace-welcome__name--interactive').count(), 0)
        if (process.env.OSS_SCREENSHOTS === '1') {
          await otherPage.waitForTimeout(1600)
          await otherPage.screenshot({ path: `/run/screenshots/workspace-welcome-${login}.png` })
        }
        await otherPage.locator('.workspace-welcome__name').click()
        assert.equal(await otherPage.locator('.workspace-welcome__particle').count(), 0)
      } finally {
        await otherContext.close()
      }
    }
    console.log('Engineer and admin see the welcome without flower interaction')
  } finally {
    await browser.close()
  }
}

main().catch((error) => { console.error(error.message); process.exitCode = 1 })
