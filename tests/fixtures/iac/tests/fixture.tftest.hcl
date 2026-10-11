# Runs in the shared workflow's Test step
run "plan" {
  command = plan

  assert {
    condition     = terraform_data.fixture.input == "ok"
    error_message = "The fixture's input should be ok."
  }
}
