# Politica de la app: solo metadatos. Nunca valores de secretos ni material de claves.

path "kv/metadata" {
  capabilities = ["list", "read"]
}

path "kv/metadata/*" {
  capabilities = ["list", "read"]
}

path "transit/keys" {
  capabilities = ["list", "read"]
}

path "transit/keys/*" {
  capabilities = ["list", "read"]
}

path "sys/mounts" {
  capabilities = ["read"]
}

path "sys/policies/acl/ens-app-metadata" {
  capabilities = ["read"]
}

path "sys/audit" {
  capabilities = ["read", "sudo"]
}

path "auth/token/accessors" {
  capabilities = ["list", "sudo"]
}

path "auth/token/lookup-accessor" {
  capabilities = ["update"]
}

path "auth/token/lookup-self" {
  capabilities = ["read"]
}

path "auth/token/renew-self" {
  capabilities = ["update"]
}

path "sys/capabilities-self" {
  capabilities = ["update"]
}

path "kv/data/*" {
  capabilities = ["deny"]
}

path "kv/subkeys/*" {
  capabilities = ["deny"]
}

path "kv/destroy/*" {
  capabilities = ["deny"]
}

path "kv/delete/*" {
  capabilities = ["deny"]
}

path "transit/export/*" {
  capabilities = ["deny"]
}

path "transit/backup/*" {
  capabilities = ["deny"]
}

path "transit/decrypt/*" {
  capabilities = ["deny"]
}

path "transit/encrypt/*" {
  capabilities = ["deny"]
}

path "transit/sign/*" {
  capabilities = ["deny"]
}

path "sys/audit/*" {
  capabilities = ["deny"]
}

path "auth/token/revoke*" {
  capabilities = ["deny"]
}

path "auth/token/renew-accessor" {
  capabilities = ["deny"]
}

path "auth/token/tidy" {
  capabilities = ["deny"]
}

path "auth/token/create*" {
  capabilities = ["deny"]
}

path "sys/generate-root-token/*" {
  capabilities = ["deny"]
}

path "sys/rekey*" {
  capabilities = ["deny"]
}

path "sys/rotate*" {
  capabilities = ["deny"]
}

path "sys/storage/raft/*" {
  capabilities = ["deny"]
}

path "sys/raw*" {
  capabilities = ["deny"]
}
