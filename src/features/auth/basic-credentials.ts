/** The server's HTTP Basic authentication accepts ASCII credentials. */
export function basicCredentials(user: string, password: string): string {
  if (!user || !password || user.includes(":") || /[^\x20-\x7e]/.test(user + password)) {
    throw new Error("Неверный логин или пароль");
  }
  return btoa(`${user}:${password}`);
}
