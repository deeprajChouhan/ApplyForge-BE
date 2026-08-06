"""
Recruiter platform module.

A self-contained module inside the ApplyForge backend that adds the AI
talent-matching product for recruiter agencies. It shares the app's
infrastructure — database engine, config, deployment — but keeps its own
`rec_`-prefixed tables with **no foreign keys into consumer tables**, so agency
data and consumer user data never mix. The consumer product is untouched; every
recruiter route lives under the `/api/v1/recruiter` prefix that existing users
never hit.

The one intended link to the consumer product (converting a CandidateProfile
into a real ApplyForge user) is a later-phase outbound call to the provisioning
endpoint, never a shared table.
"""
