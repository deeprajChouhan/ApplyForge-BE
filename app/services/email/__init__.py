"""Email: Resend transport, themed templates, outbox, automations and the operator mailbox.

See documents / claude/email-system-plan.md for the design. Entry points:
  provider.get_email_provider()      transport (Resend / console)
  consumer.queue_for_user(...)       consumer outbox
  app.recruiter.services.email       recruiter outbox (rec_*)
  events / rules                     ORM-change events + admin automations
  mailbox                            support@ / info@
"""
