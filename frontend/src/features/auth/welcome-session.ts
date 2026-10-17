const welcomeKey = 'oss-control-center:welcome-pending'

export function scheduleWelcome(userId: string): void {
  try {
    window.sessionStorage.setItem(welcomeKey, userId)
  } catch {
    // A blocked storage API must never prevent authentication.
  }
}

export function consumeWelcome(userId: string): boolean {
  try {
    if (window.sessionStorage.getItem(welcomeKey) !== userId) return false
    window.sessionStorage.removeItem(welcomeKey)
    return true
  } catch {
    return false
  }
}
