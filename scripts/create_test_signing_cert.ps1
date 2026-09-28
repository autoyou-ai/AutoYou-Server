# Create a local self-signed code signing certificate in CurrentUser\My
$certSubject = "CN=AutoYou Release Signer"
$certStore = "Cert:\CurrentUser\My"

Write-Host "Creating self-signed code-signing certificate in CurrentUser\My..."
$cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $certSubject -CertStoreLocation $certStore -NotAfter (Get-Date).AddYears(5)

Write-Host "Certificate generated successfully."
Write-Host "Subject: $($cert.Subject)"
Write-Host "Thumbprint: $($cert.Thumbprint)"

# Save thumbprint to environment for current session and user
[Environment]::SetEnvironmentVariable("AUTOYOU_WIN_CERT_THUMBPRINT", $cert.Thumbprint, "User")
[Environment]::SetEnvironmentVariable("AUTOYOU_WIN_CERT_THUMBPRINT", $cert.Thumbprint, "Process")

Write-Host "AUTOYOU_WIN_CERT_THUMBPRINT has been set to: $($cert.Thumbprint)"
