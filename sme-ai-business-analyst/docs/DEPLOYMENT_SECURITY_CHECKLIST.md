# Production Deployment Security Checklist

## Pre-Deployment Verification

### Environment Configuration
- [ ] **APP_ENV** set to `production`
- [ ] **APP_DEBUG** is `false` (verify in code)
- [ ] **SECRET_KEY** generated and set (minimum 32 characters, use `python -c "import secrets; print(secrets.token_urlsafe(32))"`)
- [ ] **SECRET_KEY** is NOT the default "change-me-before-deploy"
- [ ] All required WhatsApp variables set:
  - [ ] WHATSAPP_VERIFY_TOKEN
  - [ ] WHATSAPP_APP_SECRET
  - [ ] WHATSAPP_ACCESS_TOKEN
  - [ ] WHATSAPP_PHONE_NUMBER_ID
- [ ] At least one AI provider configured:
  - [ ] GEMINI_API_KEY OR
  - [ ] GROQ_API_KEY OR
  - [ ] OPENAI_API_KEY
- [ ] Database credentials set (not localhost)
- [ ] CORS_ORIGINS properly configured
- [ ] ALLOWED_HOSTS configured for production domain

### Database
- [ ] PostgreSQL database created and accessible
- [ ] Database connection string uses SSL (postgresql://... in connection)
- [ ] Migrations applied: `python scripts/apply_migrations.py`
- [ ] Database backups configured
- [ ] Database user has minimal privileges (no superuser)
- [ ] Connection pool size appropriate for expected load (default: 20)

### Application Code
- [ ] No hardcoded secrets in code (grep for "key=", "token=", "secret=")
- [ ] Debug mode disabled in all environments
- [ ] Logging level set to WARNING or ERROR
- [ ] Error pages don't expose stack traces
- [ ] All dependencies pinned to specific versions in requirements.txt
- [ ] No development-only packages in production requirements

### API Security
- [ ] Rate limiting configured and tested
- [ ] CORS origins restricted to known domains only
- [ ] Security headers middleware enabled
- [ ] Payload size limit enforced (1MB for webhooks)
- [ ] All admin endpoints require authentication
- [ ] JWT token expiration set appropriately (30 minutes default)

### WhatsApp Integration
- [ ] Webhook URL is HTTPS only
- [ ] Webhook endpoint properly registered with Meta
- [ ] Signature verification enabled and tested
- [ ] Webhook handler has error resilience

### Monitoring & Logging
- [ ] Application logging directed to stdout/stderr (container-friendly)
- [ ] Secrets masking enabled in logs (NO API keys in logs)
- [ ] Error tracking configured (Sentry or equivalent)
- [ ] Application metrics exposed
- [ ] Health check endpoints configured (`/health`, `/ready`)
- [ ] Log aggregation configured if using multiple instances

### Docker/Container
- [ ] Dockerfile uses non-root user
- [ ] Base image is minimal (Alpine, Slim, or Distroless)
- [ ] No secrets in Dockerfile (use environment variables)
- [ ] Container runs with readonly root filesystem if possible
- [ ] Resource limits set (memory, CPU)
- [ ] Health check configured in Docker/K8s

### SSL/TLS
- [ ] HTTPS enforced (HTTP redirects to HTTPS)
- [ ] SSL certificate valid and not self-signed
- [ ] Certificate renewal automated (Let's Encrypt)
- [ ] TLS 1.2+ only
- [ ] Strong cipher suites configured

### File Storage & Permissions
- [ ] Media download directory has restricted permissions
- [ ] Only necessary directories are world-readable
- [ ] Temporary files cleaned up regularly
- [ ] No sensitive data stored unencrypted

### Access Control
- [ ] SSH access restricted to authorized IPs only
- [ ] Firewall configured to allow only necessary ports
- [ ] Admin panel access restricted by IP (if applicable)
- [ ] Database access restricted to application only

### Backups & Disaster Recovery
- [ ] Daily database backups configured
- [ ] Backups tested for restoration
- [ ] Backup encryption enabled
- [ ] Backup retention policy set (e.g., 30 days)
- [ ] Disaster recovery plan documented

### Testing
- [ ] Security tests pass: `pytest tests/test_security.py`
- [ ] Integration tests pass in production-like environment
- [ ] Load testing shows system handles expected traffic
- [ ] Error handling tested with invalid/malicious input
- [ ] Rate limiting tested and working

### Infrastructure
- [ ] CDN configured if applicable (protects against DDoS)
- [ ] WAF (Web Application Firewall) rules configured
- [ ] DDoS mitigation enabled
- [ ] Regular vulnerability scanning scheduled
- [ ] Automated patching enabled for OS and dependencies

### Documentation
- [ ] Incident response plan documented
- [ ] Security procedures documented
- [ ] On-call contacts documented
- [ ] Deployment process documented
- [ ] Rollback procedures documented

## Post-Deployment Verification

### Health Checks
- [ ] Application starts without errors
- [ ] Health endpoints respond: `GET /health`, `GET /ready`
- [ ] Database connection working
- [ ] WhatsApp webhook responding
- [ ] Admin endpoints require authentication

### Monitoring
- [ ] Application metrics showing in monitoring system
- [ ] Logs appearing in log aggregation service
- [ ] Error alerts configured and working
- [ ] Cost alerts configured for AI providers

### User Testing
- [ ] Sample WhatsApp message processed end-to-end
- [ ] Confirmation loop working
- [ ] Daily/weekly reports generating
- [ ] Admin dashboard accessible and showing data

### Security Verification
- [ ] Security headers present in responses
  ```bash
  curl -i https://your-domain/health | grep -i "X-Frame-Options\|X-Content-Type\|Strict-Transport"
  ```
- [ ] HTTPS enforced
  ```bash
  curl -L http://your-domain/health | head -1  # Should redirect to https
  ```
- [ ] Rate limiting working
  ```bash
  for i in {1..100}; do curl -s https://your-domain/health; done | grep -c 429
  ```
- [ ] Admin endpoints protected
  ```bash
  curl https://your-domain/admin/status  # Should return 401
  ```

## Ongoing Maintenance

### Weekly
- [ ] Monitor application error rates
- [ ] Review security logs for suspicious activity
- [ ] Check database backup completion
- [ ] Review cost trends for AI providers

### Monthly
- [ ] Security patch assessment
- [ ] Dependency vulnerability scan
- [ ] Cost review and optimization
- [ ] Capacity planning review

### Quarterly
- [ ] Full security audit
- [ ] Penetration testing (if applicable)
- [ ] Disaster recovery drill
- [ ] Access control review

### Annually
- [ ] Security compliance review
- [ ] Infrastructure review
- [ ] Vendor security assessments

## Emergency Contacts & Procedures

### Security Incident Response
1. **Detection**: Set up alerts for:
   - Multiple failed authentication attempts
   - Unusual API request patterns
   - Database connection failures
   - AI cost spikes

2. **Response**:
   - [ ] Incident documented with timestamp
   - [ ] Affected systems isolated if necessary
   - [ ] Team notified
   - [ ] Investigation started
   - [ ] Data integrity verified

3. **Post-Incident**:
   - [ ] Root cause analysis
   - [ ] Remediation plan created
   - [ ] Preventive measures implemented
   - [ ] Documentation updated

## Security Tool Recommendations

### Monitoring
- Sentry (error tracking)
- DataDog (infrastructure monitoring)
- CloudFlare (DDoS protection, WAF)

### Scanning
- Dependabot (dependency updates)
- Snyk (vulnerability scanning)
- SonarQube (code quality)

### Testing
- Burp Suite (penetration testing)
- OWASP ZAP (security scanning)
- pytest (automated security tests)

## References & Further Reading

- [OWASP Deployment Checklists](https://cheatsheetseries.owasp.org/cheatsheets/Nodejs_Security_Cheat_Sheet.html)
- [FastAPI Security](https://fastapi.tiangolo.com/deployment/concepts/)
- [PostgreSQL Security](https://www.postgresql.org/docs/current/sql-syntax-lexical.html#SQL-SYNTAX-STRINGS)
- [NIST Cybersecurity Framework](https://www.nist.gov/cyberframework)
- [CIS Benchmarks](https://www.cisecurity.org/cis-benchmarks/)
