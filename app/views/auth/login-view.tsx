"use client";

import type { Dispatch, FormEvent, SetStateAction } from "react";
import { LegalFooter } from "../../legal";

type LoginViewProps = {
  loginUser: string;
  loginPassword: string;
  loginPasswordVisible: boolean;
  error: string;
  busy: boolean;
  version: string;
  commit: string;
  setLoginUser: Dispatch<SetStateAction<string>>;
  setLoginPassword: Dispatch<SetStateAction<string>>;
  setLoginPasswordVisible: Dispatch<SetStateAction<boolean>>;
  login: (event: FormEvent) => Promise<void> | void;
};

export function LoginView({ loginUser, loginPassword, loginPasswordVisible, error, busy, version, commit, setLoginUser, setLoginPassword, setLoginPasswordVisible, login }: LoginViewProps) {
  return <main className="loginPage visualLogin loginRedesign">
      <div className="loginGlow" />
      <div className="loginShell">
        <form className="loginCard" onSubmit={login}>
          <div className="loginCardHeader">
            <Logo />
            <div className="loginStatusPills">
              <span className="loginPill secure">● Secure admin access</span>
              <span className="loginPill">Control node · 312.net</span>
            </div>
          </div>

          <div className="loginIntro">
            <p className="eyebrow">INFRASTRUCTURE CONTROL</p>
            <h1>Доступ к панели управления</h1>
            <p className="loginCopy">Введите учётные данные администратора для безопасного доступа к панели управления узлом.</p>
          </div>

          <div className="loginFields">
            <label className="loginField">
              <span>Логин</span>
              <input type="text" value={loginUser} onChange={(event) => setLoginUser(event.target.value)} autoFocus required placeholder="admin" />
            </label>
            <label className="loginField">
              <span>Пароль</span>
              <div className="loginPasswordWrap">
                <input type={loginPasswordVisible ? "text" : "password"} value={loginPassword} onChange={(event) => setLoginPassword(event.target.value)} required placeholder="Введите пароль" />
                <button
                  type="button"
                  className="loginGhostButton"
                  onClick={() => setLoginPasswordVisible((value) => !value)}
                  aria-label={loginPasswordVisible ? "Скрыть пароль" : "Показать пароль"}
                >
                  {loginPasswordVisible ? "Скрыть" : "Показать"}
                </button>
              </div>
            </label>
          </div>

          {error && <div className="errorBox loginError">{error}</div>}

          <div className="loginActions">
            <button className="primaryButton loginSubmit" type="submit" disabled={busy}>
              {busy ? "Проверка доступа…" : "Войти в панель"} <span>→</span>
            </button>
            <div className="loginFootnote">
              <strong>Только для администратора</strong>
              <small>Доступ к control node выполняется через защищённую панель 312.net.</small>
            </div>
          </div>
        </form>
      </div>
      <VersionFooter version={version} commit={commit} />
    </main>;
}

function Logo() {
  return <div className="brand"><span className="brandMark"><svg viewBox="0 0 32 32" aria-hidden="true"><path d="M7 7h12l6 6v12H13l-6-6V7Z" /><path d="M11 12h8l2 2v6h-8l-2-2v-6Z" /></svg></span><div><strong>312<span>.net</span></strong><small>INFRASTRUCTURE</small></div></div>;
}

function VersionFooter({ version, commit }: { version: string; commit: string }) {
  return <LegalFooter version={version} commit={commit} />;
}
