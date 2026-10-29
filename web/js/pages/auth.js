// Sign-in and first-time account setup (shown instead of the app when sign-in is enabled and nobody is signed in).
import { h, icon, Button, Field, Input, Alert, Segmented, busy, nextId } from '../kit.js';
import { api } from '../api.js';

function PasswordInput({ id, placeholder = '', autocomplete = 'current-password', onEnter }) {
  const input = Input({ id, type: 'password', placeholder, autocomplete });
  input.classList.add('pr-10');
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && onEnter) onEnter(); });
  const eye = h('button', { type: 'button', class: 'absolute right-2 top-2 rounded p-0.5 text-subtle hover:text-ink', 'aria-label': 'Show password', onClick: () => {
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    eye.replaceChildren(icon(show ? 'eyeoff' : 'eye', 'h-4 w-4'));
    eye.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
  } }, icon('eye', 'h-4 w-4'));
  const wrap = h('div', { class: 'relative' }, input, eye);
  wrap.input = input;
  return wrap;
}

export function AuthPage({ onDone }) {
  let mode = 'signin';
  let challenge = '';
  const ids = { email: nextId(), pw: nextId(), code: nextId(), pw2: nextId(), npw: nextId(), tfa: nextId() };
  const msg = h('div', { class: 'mb-4', 'aria-live': 'polite' });
  const form = h('div', { class: 'space-y-4' });

  const email = Input({ id: ids.email, type: 'email', placeholder: 'you@example.com', autocomplete: 'username' });
  const pw = PasswordInput({ id: ids.pw, onEnter: () => submit() });
  const code = Input({ id: ids.code, placeholder: 'XXXX-XXXX-XXXX-XXXX', mono: true, autocomplete: 'one-time-code' });
  const npw = PasswordInput({ id: ids.npw, autocomplete: 'new-password', placeholder: 'At least 10 characters' });
  const npw2 = PasswordInput({ id: ids.pw2, autocomplete: 'new-password', placeholder: 'Type it again', onEnter: () => submit() });
  const tfa = Input({ id: ids.tfa, placeholder: '123456', mono: true, autocomplete: 'one-time-code' });
  tfa.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
  const go = Button({ label: 'Sign in', variant: 'primary', class: 'w-full', onClick: () => submit() });
  email.addEventListener('keydown', (e) => { if (e.key === 'Enter') (mode === 'signin' ? pw.input : code).focus(); });

  function paint() {
    msg.replaceChildren();
    segWrap.classList.toggle('hidden', mode === '2fa');   // not the hidden attribute: the flex class would override it
    go.lastChild.textContent = mode === 'signin' ? 'Sign in' : mode === '2fa' ? 'Verify' : 'Set password and sign in';
    if (mode === '2fa') {
      form.replaceChildren(
        Field({ label: 'Authentication code', id: ids.tfa, hint: 'The 6-digit code from your authenticator app, or one of your recovery codes.' }, tfa), go,
        h('button', { type: 'button', class: 'block w-full text-center text-xs text-muted hover:text-ink', onClick: () => { mode = 'signin'; challenge = ''; tfa.value = ''; paint(); } }, 'Back to sign in'));
      tfa.focus();
      return;
    }
    form.replaceChildren(
      Field({ label: 'Email', id: ids.email }, email),
      mode === 'signin'
        ? Field({ label: 'Password', id: ids.pw }, pw)
        : h('div', { class: 'space-y-4' },
          Field({ label: 'Activation code', id: ids.code, hint: 'Given to you by the person who added your account.' }, code),
          Field({ label: 'Choose a password', id: ids.npw, hint: 'At least 10 characters, with letters and numbers or symbols.' }, npw),
          Field({ label: 'Repeat the password', id: ids.pw2 }, npw2)),
      go);
  }

  async function submit() {
    msg.replaceChildren();
    try {
      let r;
      if (mode === '2fa') {
        r = await busy(go, () => api('/api/auth/login-2fa', { challenge, code: tfa.value }));
      } else if (mode === 'signin') {
        r = await busy(go, () => api('/api/auth/login', { email: email.value, password: pw.input.value }));
      } else {
        if (npw.input.value !== npw2.input.value) throw new Error('The two passwords do not match');
        r = await busy(go, () => api('/api/auth/activate', { email: email.value, code: code.value, password: npw.input.value }));
      }
      if (r.need_2fa) { challenge = r.challenge; mode = '2fa'; paint(); return; }
      onDone();
    } catch (e) {
      msg.replaceChildren(Alert({ tone: 'bad' }, e.message));
    }
  }

  const seg = Segmented({ label: 'Sign in or set up', value: mode, onChange: (v) => { mode = v; paint(); },
    options: [{ value: 'signin', label: 'Sign in' }, { value: 'activate', label: 'Set up your account' }] });
  const segWrap = h('div', { class: 'mb-5 flex justify-center' }, seg);
  paint();
  email.focus();
  return h('div', { class: 'flex min-h-screen items-center justify-center px-4 py-10' },
    h('div', { class: 'w-full max-w-sm animate-fade-up' },
      h('div', { class: 'mb-8 flex flex-col items-center text-center' },
        h('span', { class: 'mb-4 flex h-12 w-12 items-center justify-center rounded-xl bg-brand text-brand-ink' }, icon('terminal', 'h-6 w-6')),
        h('h1', { class: 'text-xl' }, 'Shiplane'),
        h('p', { class: 'mt-1 text-sm text-muted' }, 'Sign in to manage sites and servers.')),
      h('div', { class: 'card p-6' }, segWrap, msg, form),
      h('p', { class: 'mt-6 text-center text-xs text-subtle' }, 'Access is limited to approved accounts. Too many wrong attempts lock sign-in for a few minutes.')));
}
