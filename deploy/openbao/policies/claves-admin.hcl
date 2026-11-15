# Politica de la persona administradora (userpass). Sin sudo, sin escritura en politicas ni auditoria.

path "kv/*" {
  capabilities = ["create", "read", "update", "patch", "delete", "list", "scan"]
}

path "transit/*" {
  capabilities = ["create", "read", "update", "delete", "list"]
}

path "transit/export/*" {
  capabilities = ["deny"]
}

path "transit/backup/*" {
  capabilities = ["deny"]
}

path "sys/mounts" {
  capabilities = ["read"]
}

path "sys/policies/acl" {
  capabilities = ["list", "read"]
}

path "sys/policies/acl/*" {
  capabilities = ["list", "read"]
}

path "auth/token/create/ens-app" {
  capabilities = ["update"]
}

path "sys/generate-root-token/*" {
  capabilities = ["create", "read", "update", "delete"]
}

path "auth/userpass/users/admin/password" {
  capabilities = ["update"]
}
