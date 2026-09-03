# IBM MQ Objects as Code

Ansible-based automation for managing IBM MQ objects using a YAML-defined desired state.

The project allows IBM MQ administrators and engineers to define MQ objects in YAML and automatically create objects that do not already exist on the target queue manager.

## Project Overview

This project follows an **MQ Objects as Code** approach.

Instead of defining MQ objects manually using MQSC commands, the desired MQ configuration is maintained in a YAML file.

Ansible reads the YAML configuration, checks whether each object already exists on the target queue manager, and creates only the objects that are missing.

qm1 ansible_host=<MQ_SERVER_IP> ansible_user=<SSH_USER>  ## user should add their own IP address and user name in <MQ_SERVER_IP> and <SSH_USER>

### Current workflow

```text
mq_objects.yml
      |
      v
Ansible
      |
      v
Check MQ object
      |
      +-------------------+
      |                   |
   Exists              Missing
      |                   |
      v                   v
   Skip                 Create


---


## Key Design

Each MQ object contains a `host_qmgr` attribute.

This determines which queue manager the object belongs to.

Example:

```yaml
mq_queues:

  - name: TESTQ1
    type: local
    host_qmgr: QM1
    max_depth: 5000

  - name: REMOTE.Q1
    type: remote
    host_qmgr: QM1
    rname: TESTQ1.QM2
    remote_qmgr: QM2
    xmit_queue: TESTQ4


---

### Queues

The current version supports:

- Local queues
- Transmission queues
- Remote queues
- Alias queues

### Channels

The current version supports:

- Sender (SDR)
- Receiver (RCVR)


## Supported MQ Objects

### Queues

The current version supports:

- Local queues
- Transmission queues
- Remote queues
- Alias queues

### Channels

The current version supports:

- Sender (SDR)
- Receiver (RCVR)


## Automation Logic

### Queue processing

For every queue defined in `mq_objects.yml`:

1. Ansible checks the queue on the specified `host_qmgr`.
2. If the queue exists, no change is made.
3. If the queue does not exist, Ansible creates it.
4. The queue type determines the MQSC command used for creation.

### Channel processing

For every channel defined in `mq_objects.yml`:

1. Ansible checks the channel on the specified `host_qmgr`.
2. If the channel exists, no change is made.
3. If the channel does not exist, Ansible creates it.
4. SDR and RCVR channels use different MQSC definitions.


## Production Safety

This project intentionally follows a **create-if-missing** model.

The automation does **not** automatically:

- Delete existing MQ objects
- Recreate existing MQ objects
- Alter existing MQ object attributes
- Replace existing production configuration

This is important in production environments because an existing MQ object may already be in use by an application, integration, or another user.

Changes to existing production MQ objects should be reviewed by an MQ administrator before implementation.

## Project Structure

```text
mq_objects_as_code/
│
├── inventory/
│   └── hosts
│
├── playbooks/
│   └── configure_mq.yml
│
├── vars/
│   └── mq_objects.yml
│
├── .gitignore
│
└── README.md

## Running the Automation

### Syntax check

Before running the playbook:

```bash
ansible-playbook -i inventory/hosts playbooks/configure_mq.yml --syntax-check

ansible-playbook -i inventory/hosts playbooks/configure_mq.yml

## If SSH password authentication is being used.

ansible-playbook -i inventory/hosts playbooks/configure_mq.yml --ask-pass --ask-become-pass

## Current Status

### Implemented

- [x] YAML-based MQ object definitions
- [x] Queue existence checking
- [x] Local queue creation
- [x] Transmission queue creation
- [x] Remote queue creation
- [x] Alias queue creation
- [x] Channel existence checking
- [x] SDR channel creation
- [x] RCVR channel creation
- [x] `host_qmgr` based object targeting
- [x] Create-if-missing logic
- [x] Protection against modifying existing objects

### Future Enhancements

- [ ] Listener automation
- [ ] MQ authentication and authorization objects
- [ ] Channel authentication records (CHLAUTH)
- [ ] Queue manager configuration
- [ ] Ansible roles
- [ ] CI/CD integration
- [ ] Jenkins pipeline
- [ ] Automated testing

## Author

**Ankur Lodhi**

IBM MQ | Solace | MFT | Ansible Automation

This project demonstrates the application of Infrastructure as Code and automation principles to IBM MQ administration.