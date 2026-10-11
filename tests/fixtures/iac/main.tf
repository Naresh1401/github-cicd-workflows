terraform {
  required_version = ">= 1.6.0"
}

resource "terraform_data" "fixture" {
  input = "ok"
}
