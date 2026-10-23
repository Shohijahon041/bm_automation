# Security Audit Prompt

## Maqsod
Loyihani xavfsizlik bo'yicha to'liq audit qilish.

## Kontekst
- Loyiha: BM Automation (Transport operations)
- Xavfsizlik qavatlari: Network, Application, Data, Operational

## Audit Ro'yxati

### 1. Secrets Management
- [ ] `.env` gitignore'da mi?
- [ ] `tokens.json`, `profiles.json`, `state/` gitignore'da mi?
- [ ] Hardcoded secretlar yo'qmi? (`grep -r "password\|token\|secret" --include="*.py"`)
- [ ] Tokenlar fayl tizimida qanday saqlanadi? (chmod 600)
- [ ] Token refresh/rotate mexanizmi bor-mi?

### 2. Authentication & Authorization
- [ ] Role-based access control (RBAC) barcha endpointlarda?
- [ ] Session management: httpOnly, Secure, SameSite cookie?
- [ ] Brute-force protection (login, API)?
- [ ] Session timeout / rotation?
- [ ] Privilege escalation mumkinmi?

### 3. Input Validation & Injection
- [ ] SQL injection: parameterized queries / ORM?
- [ ] Path traversal: `../` filtering?
- [ ] XSS: output encoding, CSP headers?
- [ ] Command injection: `subprocess` / `os.system` yo'qmi?
- [ ] Deserialization attacks (pickle, yaml)?

### 4. Network & Transport
- [ ] HTTPS enforced (HSTS)?
- [ ] CORS policy restrictive mi?
- [ ] Security headers: X-Frame-Options, X-Content-Type-Options, Referrer-Policy, CSP?
- [ ] Rate limiting (API, login, bot)?

### 5. Logging & Monitoring
- [ ] Audit log (login, admin actions, data access)?
- [ ] Secretlar loglarda chiqmayaptimi?
- [ ] Error handling: stack trace userga bermayaptimi?
- [ ] Alerting on suspicious activity?

### 6. Dependencies
- [ ] `pip audit` / `safety check` o'tganmi?
- [ ] Outdated packages yo'qmi?
- [ ] Supply chain: pinned versions, hash verification?

### 7. Operational
- [ ] Backup encryption?
- [ ] Disaster recovery plan?
- [ ] Incident response playbook?
- [ ] Key rotation schedule?

## Alatli Audit

```bash
# Secrets scan
grep -r "password\|token\|secret\|api_key" --include="*.py" --include="*.json" . | grep -v test | grep -v ".git"

# Hardcoded credentials
grep -rE "(password|token|secret)\s*=\s*['\"][^'\"]{8,}" --include="*.py" .

# SQL injection risk
grep -r "execute.*%\|execute.*+" --include="*.py" .

# Path traversal
grep -r "open.*\.\.\|Path.*\.\." --include="*.py" .

# Security headers check
curl -I https://bmbrutto.uz/ | grep -i "x-frame\|x-content\|referrer\|csp\|hsts"

# Dependencies
pip audit
safety check
```

## Kutilgan Natija
- Topilgan zaifliklar ro'yxati (CVSS severity bilan)
- Har bir zaiflik uchun: tasvir, ta'sir, tuzatish yo'llari
- Ustuvorlik tartibida saralash
- Remediation timeline