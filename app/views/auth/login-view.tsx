"use client";

import type { Dispatch, FormEvent, SetStateAction } from "react";
import { BrandGlyph } from "../../components/brand-glyph";
import { LegalFooter } from "../../legal";

type LoginViewProps = { loginUser: string; loginPassword: string; loginPasswordVisible: boolean; error: string; busy: boolean; version: string; commit: string; setLoginUser: Dispatch<SetStateAction<string>>; setLoginPassword: Dispatch<SetStateAction<string>>; setLoginPasswordVisible: Dispatch<SetStateAction<boolean>>; login: (event: FormEvent) => Promise<void> | void; };

export function LoginView({ loginUser, loginPassword, loginPasswordVisible, error, busy, version, commit, setLoginUser, setLoginPassword, setLoginPasswordVisible, login }: LoginViewProps) {
  return (
    <main className="loginPage visualLogin loginRedesign">
      <div className="loginBackdrop" aria-hidden="true" />
      <section className="loginLayout" aria-label="Вход в панель управления">
        <aside className="loginHero">
          <Logo />
          <div className="loginHeroCopy">
            <p className="loginKicker"><span /> CONTROL &amp; MANAGEMENT</p>
            <h1>Контроль системы.<br /><em>Управление в одном месте.</em></h1>
            <p>Единый интерфейс для контроля состояния приложения, управления сервисами и конфигурацией сервера.</p>
          </div>
          <div className="loginHeroStatus"><span className="loginPulse" /><div><strong>CONTROL NODE</strong><small>Защищённый административный контур</small></div></div>
        </aside>

        <div className="loginPanelWrap">
          <form className="loginPanel" onSubmit={login}>
            <header className="loginPanelHeader"><span className="loginLock"><LockIcon /></span><div><p>MANAGEMENT ACCESS</p><h2>Вход в панель управления</h2></div></header>
            <p className="loginPanelCopy">Авторизуйтесь для контроля и управления приложением.</p>
            <div className="loginFields">
              <label className="loginField"><span>Логин</span><div className="loginInputWrap"><UserIcon /><input type="text" value={loginUser} onChange={(event) => setLoginUser(event.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false} autoFocus required placeholder="admin" /></div></label>
              <label className="loginField"><span>Пароль</span><div className="loginInputWrap loginPasswordWrap"><KeyIcon /><input type={loginPasswordVisible ? "text" : "password"} value={loginPassword} onChange={(event) => setLoginPassword(event.target.value)} autoComplete="current-password" required placeholder="Введите пароль" /><button type="button" className="loginVisibility" onClick={() => setLoginPasswordVisible((value) => !value)} aria-label={loginPasswordVisible ? "Скрыть пароль" : "Показать пароль"} aria-pressed={loginPasswordVisible}>{loginPasswordVisible ? <EyeOffIcon /> : <EyeIcon />}</button></div></label>
            </div>
            {error && <div className="errorBox loginError" role="alert">{error}</div>}
            <button className="loginSubmit" type="submit" disabled={busy}><span>{busy ? "Проверяем доступ…" : "Войти в панель"}</span>{busy ? <i className="loginSpinner" /> : <ArrowIcon />}</button>
            <footer className="loginPanelFooter"><ShieldIcon /><span>Соединение с панелью защищено</span></footer>
          </form>
        </div>
      </section>
      <LegalFooter version={version} commit={commit} />
    </main>
  );
}

function Logo() { return <div className="loginBrand"><span><BrandGlyph /></span><div><strong>312<em>.net</em></strong><small>INFRASTRUCTURE</small></div></div>; }
function LockIcon() { return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="10" width="14" height="10" rx="3" /><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v2" /></svg>; }
function UserIcon() { return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="3.5" /><path d="M5.5 19c.8-3.4 3-5.1 6.5-5.1s5.7 1.7 6.5 5.1" /></svg>; }
function KeyIcon() { return <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="8" cy="12" r="3.5" /><path d="M11.5 12H20m-3 0v3m-3-3v2" /></svg>; }
function EyeIcon() { return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12s3.2-5 9-5 9 5 9 5-3.2 5-9 5-9-5-9-5Z" /><circle cx="12" cy="12" r="2.5" /></svg>; }
function EyeOffIcon() { return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 4 16 16M9.8 7.2A9.8 9.8 0 0 1 12 7c5.8 0 9 5 9 5a15 15 0 0 1-2.1 2.6M6.2 8.2A15.2 15.2 0 0 0 3 12s3.2 5 9 5c1 0 2-.2 2.8-.4" /></svg>; }
function ArrowIcon() { return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14m-5-5 5 5-5 5" /></svg>; }
function ShieldIcon() { return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 5.5 5.8v5.1c0 4.3 2.6 7.8 6.5 9.1 3.9-1.3 6.5-4.8 6.5-9.1V5.8L12 3Z" /><path d="m9.3 11.8 1.8 1.8 3.8-4" /></svg>; }
