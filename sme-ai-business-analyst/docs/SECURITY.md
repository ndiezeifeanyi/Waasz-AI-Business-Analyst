# Security Architecture & Implementation Guide

## Overview

This document describes the production-grade security architecture implemented in the SME AI Business Analyst Platform. The system is designed to protect sensitive financial data from informal SMEs in Nigeria/Africa while maintaining ease of use.

## Security Layers

### 1. Authentication & Authorization

#### JWT Token-Based Authentication
- **Implementation**: `app/core/auth.py`
- **Token Type**: HS256-signed JWT
- **Token Claims**: 
  - `sub`: User ID
  - `role`: User role (admin, business_owner, staff, system)
  - `business_id`: Associated business (for non-admins)
  - `exp`: Expiration (30 minutes)
  - `iat`: Issued at
  - `jti`: Token ID (for revocation)

#### Password Security
- **Algorithm**: Argon2 (via passlib)
- **Configuration**: Automatic, secure defaults
- **Hash Verification**: Constant-time comparison to prevent timing attacks

#### Role-Based Access Control (RBAC)
- **Roles**:
  - `admin`: Full system access, metrics, user management
  - `business_owner`: Access to own business records
  - `staff`: Limited access to business records
  - `system`: Internal system tasks only

- **Permission Model**: Role → Permissions mapping in `RoleBasedAccessControl`
- **Enforcement**: `@require_admin`, `@require_permission`, `@require_business_access` decorators

### 2. API Security

#### Rate Limiting
- **Location**: `app/core/rate_limit.py`
- **Strategy**: Per-IP rate limiting with time-windowed bucket algorithm
- **Limits**:
  - Webhooks: 30 requests/minute (configurable)
  - General API: 60 requests/minute
  - Admin endpoints: 10 requests/minute (stricter)

#### Middleware Security Stack
- **Location**: `app/core/security_middleware.py`

1. **Security Headers Middleware**
   - X-Frame-Options: DENY (clickjacking protection)
   - X-Content-Type-Options: nosniff (MIME sniffing prevention)
   - X-XSS-Protection: 1; mode=block
   - Content-Security-Policy: Restrictive defaults
   - Strict-Transport-Security: 31536000s (HSTS, production only)

2. **Rate Limit Middleware**
   - Per-IP request tracking
   - Endpoint-specific limits
   - Time-window sliding algorithm

3. **Payload Size Middleware**
   - Maximum payload: 50MB
   - Protects against memory exhaustion attacks

4. **Input Sanitization Middleware**
   - Detects SQL injection patterns
   - Blocks suspicious query parameters
   - Regex-based pattern matching

5. **Exception Handling Middleware**
   - Prevents stack trace leakage in production
   - Generic error messages for users
   - Detailed logging for debugging

#### CORS Configuration
- **Location**: Configured in `app/main.py`
- **Allowed Origins**: Configured via `CORS_ORIGINS` environment variable
- **Allowed Methods**: GET, POST, OPTIONS
- **Credentials**: Supported with same-origin requirement
- **Max Age**: 600 seconds

#### Secure Headers
- **Set by**: `SecurityHeadersMiddleware`
- **Applied to**: All responses
- **Production-Only Headers**: HSTS, stricter CSP

### 3. WhatsApp Webhook Security

#### Signature Verification
- **Location**: `app/core/security.py`
- **Algorithm**: HMAC-SHA256
- **Secret**: `WHATSAPP_APP_SECRET` (environment-based)
- **Key Protection**:
  - **FAIL-SECURE**: Missing secret → verification fails (not skips)
  - Constant-time comparison prevents timing attacks
  - All requests without valid signature rejected with 403

#### Webhook Validation
- **Timestamp Validation**: Prevents replay attacks
- **Payload Size Limits**: 1MB maximum per request
- **Rate Limiting**: Per-IP rate limiting
- **Idempotency**: Deduplication on `Webhook-ID` header

#### Processing
- **Async Processing**: Background tasks prevent blocking
- **Error Handling**: Exceptions logged, webhook returns 202 immediately
- **Retry Logic**: Exponential backoff on failures

### 4. Input Validation & Sanitization

#### Text Input
- **Function**: `sanitize_text()`
- **Validates**:
  - Max length enforcement (4000 chars default)
  - Null byte removal
  - Control character removal
  - Whitespace normalization

#### Phone Numbers
- **Function**: `sanitize_phone_number()`
- **Format**: International format with validation
- **Range**: 8-20 digits
- **Pattern**: Allows `+` and digits only

#### Currency Amounts
- **Function**: `sanitize_currency_amount()`
- **Range**: ₦0 to ₦999,999,999.99
- **Precision**: 2 decimal places
- **Validation**: Prevents negative amounts

#### URL Validation
- **Function**: `sanitize_url()`
- **Protocol**: https:// or http:// only
- **Max Length**: 2048 characters
- **Security**: No null bytes or control characters

### 5. AI Security

#### Prompt Injection Detection
- **Location**: `app/core/sanitization.py::detect_prompt_injection()`
- **Patterns Detected**:
  - "ignore previous instructions"
  - "system override"
  - "jailbreak"
  - "roleplay as"
  - "pretend you are"
  - Other common injection techniques

#### Cost Monitoring
- **Location**: `app/services/cost_monitor.py`
- **Limits**:
  - Per-business daily limit: $5 (configurable)
  - Per-business monthly limit: $100 (configurable)
  - Global daily spend tracking

#### Token Cost Tracking
- **Per Operation**: Extraction, confirmation, OCR, voice
- **Provider-Specific**: Different pricing models
- **Estimated Costs**: Calculated pre-request

#### AI Output Validation
- **Schema Validation**: Pydantic models ensure structure
- **Confidence Scoring**: Only persist high-confidence extractions
- **Fallback Logic**: Local heuristic extraction if AI unavailable

### 6. Database Security

#### Connection Security
- **Pool Configuration**: 
  - Size: 20 connections (configurable)
  - Max overflow: 10 connections
  - Pre-ping: Validates connections before use

#### SSL/TLS
- **Production**: SSL required for PostgreSQL connections
- **Development**: SSL optional

#### Query Safety
- **Parameterized Queries**: SQLAlchemy ORM enforces this
- **No String Interpolation**: All queries use SQLAlchemy Query API
- **Statement Timeout**: 5 minutes (production)

#### Sensitive Data
- **Encrypted Fields**: (Future: Add encryption-at-rest for financial data)
- **Access Logging**: All database access logged
- **Audit Trail**: Changes tracked in audit tables

### 7. Secrets Management

#### Environment-Based Secrets
- **No Defaults**: All secrets are empty by default
- **Required in Production**: 
  - `SECRET_KEY`
  - `WHATSAPP_APP_SECRET`
  - `WHATSAPP_ACCESS_TOKEN`
  - `WHATSAPP_PHONE_NUMBER_ID`
  - `WHATSAPP_VERIFY_TOKEN`

#### Startup Validation
- **Location**: `app/core/config.py::Settings.validate_at_startup()`
- **Checks**:
  - Required secrets present
  - No default values in production
  - At least one AI provider configured
  - Debug mode not enabled in production

#### Logging Protection
- **Secrets Masking**: `app/core/logging.py::SecretsMaskingFormatter`
- **Pattern Matching**: Identifies and masks:
  - API keys
  - Tokens
  - Passwords
  - Bearer credentials
- **Display Format**: Shows first/last 3 chars only

### 8. Error Handling

#### User-Facing Errors
- **Production**: Generic error messages
- **Development**: Detailed error information
- **Never Exposed**: Stack traces, SQL queries, internal paths

#### Error Logging
- **Structured Logging**: Context information captured
- **Security Incidents**: Separate alert channels
- **PII Protection**: Personal identifiable information masked

#### Graceful Degradation
- **AI Fallback**: Local extraction if all providers fail
- **Database Fallback**: Proper exception handling
- **WhatsApp Fallback**: Retry logic with exponential backoff

### 9. Deployment Security

#### Configuration by Environment
```
- local: Debug on, lenient validation, no HTTPS required
- test: Debug off, full validation, test database
- staging: Debug off, full validation, production-like
- production: Debug OFF (enforced), strict validation, SSL required
```

#### Docker Security
- **Run as Non-Root**: Specify user in Dockerfile
- **No Secrets in Image**: Use environment variables only
- **Minimal Base Image**: Alpine or slim variants
- **Vulnerability Scanning**: Regular image scanning

#### Deployment Checklist
- [ ] All secrets configured via environment variables
- [ ] `APP_ENV=production`
- [ ] `APP_DEBUG=false` (enforced)
- [ ] `SECRET_KEY` changed (minimum 32 bytes)
- [ ] SSL certificate valid
- [ ] Rate limiting tested
- [ ] Backup strategy in place
- [ ] Monitoring configured
- [ ] Access logs enabled
- [ ] Database backups automated

### 10. Monitoring & Incident Response

#### Security Logging
- **Webhook Verification**: All verification attempts logged
- **Authentication**: Login successes/failures logged
- **Authorization**: Permission denials logged
- **API Abuse**: Rate limit violations logged
- **Cost Alerts**: AI spending exceeding limits logged

#### Metrics to Monitor
- Request rate per IP
- Authentication failure rate
- Authorization denial rate
- Webhook verification failures
- Database connection pool usage
- AI cost trends
- Error rate by type

#### Alert Conditions
- Multiple authentication failures
- Rate limit violations
- Unexpected AI cost spikes
- Database connection pool exhaustion
- Webhook signature verification failures
- Unhandled exceptions

## Threat Model

### Threats Addressed

1. **API Abuse**
   - ✅ Rate limiting per IP
   - ✅ Endpoint-specific limits
   - ✅ Payload size limits

2. **Prompt Injection**
   - ✅ Pattern detection
   - ✅ Confidence scoring
   - ✅ Local fallback extraction

3. **Unauthorized Access**
   - ✅ JWT authentication
   - ✅ RBAC enforcement
   - ✅ Business isolation

4. **Webhook Spoofing**
   - ✅ HMAC-SHA256 signature verification
   - ✅ Fail-secure implementation
   - ✅ Replay attack prevention

5. **SQL Injection**
   - ✅ Parameterized queries (SQLAlchemy ORM)
   - ✅ Input sanitization
   - ✅ Query input validation

6. **Cross-Tenant Data Leakage**
   - ✅ RBAC enforcement
   - ✅ Business isolation checks
   - ✅ Audit logging

7. **Malicious File Uploads**
   - ✅ MIME type validation (future)
   - ✅ File size limits
   - ✅ Virus scanning hooks
   - ✅ Secure temporary storage

8. **AI Output Manipulation**
   - ✅ Schema validation
   - ✅ Confidence scoring
   - ✅ Confirmation loop required

9. **DDoS/Rate Abuse**
   - ✅ Per-IP rate limiting
   - ✅ Connection pooling
   - ✅ Payload size limits

10. **Secrets Leakage**
    - ✅ Environment-based secrets
    - ✅ Log masking
    - ✅ Startup validation

## Testing Security

### Unit Tests
```bash
pytest tests/test_security.py -v
```

### Manual Testing

1. **Webhook Verification**
   ```bash
   # Should fail
   curl -X GET "http://localhost:8000/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=wrong"
   
   # Should succeed
   curl -X GET "http://localhost:8000/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=YOUR_TOKEN&hub.challenge=test"
   ```

2. **Rate Limiting**
   ```bash
   # Rapid requests should hit limit
   for i in {1..50}; do curl -s http://localhost:8000/health; done
   ```

3. **Input Validation**
   ```bash
   # Should be rejected
   curl -X POST http://localhost:8000/webhooks/whatsapp \
     -H "Content-Type: application/json" \
     -d '{"test": "'; DROP TABLE users; --"}'
   ```

## Compliance

- ✅ OWASP Top 10 protection
- ✅ NIST cybersecurity framework basics
- ✅ Secure coding practices
- ✅ Defense-in-depth architecture
- ⚠️ Data privacy (future: encryption-at-rest, PII masking)
- ⚠️ GDPR compliance (future: data retention policies)

## Future Enhancements

1. **Encryption at Rest**
   - Database field-level encryption
   - Media file encryption

2. **Advanced Monitoring**
   - Sentry integration
   - Real-time alerting
   - Security dashboards

3. **Audit Trail**
   - Immutable audit logs
   - Change tracking
   - Compliance reports

4. **Advanced Threat Detection**
   - Anomaly detection
   - Behavioral analysis
   - ML-based abuse detection

5. **Enhanced API Security**
   - API key management
   - Request signing
   - OAuth 2.0 support

## References

- OWASP: https://owasp.org/www-project-top-ten/
- NIST Cybersecurity Framework: https://www.nist.gov/cyberframework
- FastAPI Security: https://fastapi.tiangolo.com/tutorial/security/
- SQLAlchemy Security: https://docs.sqlalchemy.org/en/20/
- JWT Best Practices: https://tools.ietf.org/html/rfc8725
