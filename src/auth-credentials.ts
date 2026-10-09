export function basicCredentials(username: string, password: string): string {
  if (!/^[\x21-\x7e]+$/.test(username) || username.includes(":")) {
    throw new Error("Логин должен содержать латинские символы без пробелов и двоеточия");
  }
  if (!/^[\x21-\x7e]+$/.test(password)) {
    throw new Error("Пароль должен содержать печатные латинские символы без пробелов");
  }
  return btoa(`${username}:${password}`);
}
