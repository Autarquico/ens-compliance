ui = true
api_addr = "http://openbao:8200"
cluster_addr = "http://openbao:8201"

storage "raft" {
  path = "/openbao/data"
  node_id = "caja"
}

listener "tcp" {
  address = "0.0.0.0:8200"
  tls_disable = true
}

audit "file" "archivo" {
  description = "Auditoria de toda peticion a OpenBao"
  options {
    file_path = "/openbao/audit/audit.log"
    mode = "0600"
  }
}
