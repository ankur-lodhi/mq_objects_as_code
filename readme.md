IBM MQ Objects as Code

Ansible-based automation for managing IBM MQ objects using a YAML-defined desired state.

The project allows IBM MQ administrators and engineers to define MQ objects in YAML and automatically create objects that do not already exist on the target queue manager.

Project Overview

This project follows an MQ Objects as Code approach.

Instead of defining MQ objects manually using MQSC commands, the desired MQ configuration is maintained in YAML files.

Ansible reads the YAML configuration, checks whether each object already exists on the target queue manager, and creates only the objects that are missing.

qm1 ansible_host=<MQ_SERVER_IP> ansible_user=<SSH_USER> ## user should add their own IP address and user name in <MQ_SERVER_IP> and <SSH_USER>

Current workflow
mq_objects.yml

      |

      v

Ansible

      |

      v

Check MQ object

      +-------------------+
      |                   |
   Exists              Missing
      |                   |
      v                   v
    Skip                Create
Key Design

Each MQ object contains a host_qmgr attribute.

This determines which queue manager the object belongs to.

Example:

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
Queues

The current version supports:

Local queues
Transmission queues
Remote queues
Alias queues
Channels

The current version supports:

Sender (SDR)
Receiver (RCVR)
Listeners

The current version supports:

TCP listeners
Queue manager controlled listeners
Topics

The current version supports:

Topic definitions
Subscriptions

The current version supports:

MQ subscriptions
Subscription destination queues
Authority Records

The current version supports:

Queue manager authority records
Queue authority records
Topic authority records
Channel Authentication Records

The current version supports:

BLOCKUSER rules
SSLPEERMAP rules
Idempotent CHLAUTH creation
CHLAUTH security refresh
Supported MQ Objects
Queues

The current version supports:

Local queues
Transmission queues
Remote queues
Alias queues
Channels

The current version supports:

Sender (SDR)
Receiver (RCVR)
Listeners

The current version supports:

TCP listeners
Queue manager controlled listeners
Topics

The current version supports:

Topic definitions
Subscriptions

The current version supports:

MQ subscriptions
Subscription destination queues
Authority Records

The current version supports:

Queue manager authority records
Queue authority records
Topic authority records
Channel Authentication Records

The current version supports:

BLOCKUSER
SSLPEERMAP
Automation Logic
Queue processing

For every queue defined in mq_queues.yml:

Ansible checks the queue on the specified host_qmgr.
If the queue exists, no change is made.
If the queue does not exist, Ansible creates it.
The queue type determines the MQSC command used for creation.
Channel processing

For every channel defined in mq_channels.yml:

Ansible checks the channel on the specified host_qmgr.
If the channel exists, no change is made.
If the channel does not exist, Ansible creates it.
SDR and RCVR channels use different MQSC definitions.
Listener processing

For every listener defined in mq_listeners.yml:

Ansible checks the listener on the specified host_qmgr.
If the listener exists, no change is made.
If the listener does not exist, Ansible creates it.
Topic processing

For every topic defined in mq_topics.yml:

Ansible checks the topic on the specified host_qmgr.
If the topic exists, no change is made.
If the topic does not exist, Ansible creates it.
Subscription processing

For every subscription defined in mq_subscriptions.yml:

Ansible checks the subscription on the specified host_qmgr.
If the subscription exists, no change is made.
If the subscription does not exist, Ansible creates it.
Authority record processing

For every authority record defined in mq_auth_records.yml:

Ansible applies the authority record to the specified host_qmgr.
The authority record defines the required permissions for the MQ object.
The configuration is targeted using the host_qmgr attribute.
CHLAUTH processing

For every CHLAUTH record defined in mq_chlauth.yml:

Ansible checks whether the CHLAUTH record already exists.
If the record exists, no change is made.
If the record does not exist, Ansible creates it using the configured ACTION.
CHLAUTH security is refreshed once for each unique queue manager.
Production Safety

This project intentionally follows a create-if-missing model.

The automation does not automatically:

Delete existing MQ objects
Recreate existing MQ objects
Alter existing MQ object attributes
Replace existing production configuration

This is important in production environments because an existing MQ object may already be in use by an application, integration, or another user.

Changes to existing production MQ objects should be reviewed by an MQ administrator before implementation.

The project is designed to add missing configuration while minimizing the risk of disrupting existing working MQ objects.

Project Structure
mq_objects_as_code/

│
├── inventory/
│   ├── hosts
│   └── group_vars/
│       ├── all.yml
│       └── mqservers.yml
│
├── playbooks/
│   └── configure_mq.yml
│
├── tasks/
│   ├── configure_queues.yml
│   ├── configure_channels.yml
│   ├── configure_listeners.yml
│   ├── configure_topics.yml
│   ├── configure_subscriptions.yml
│   ├── configure_authrecords.yml
│   └── configure_chlauth.yml
│
├── vars/
│   ├── queues.yml
│   ├── channels.yml
│   ├── listeners.yml
│   ├── topics.yml
│   ├── subscriptions.yml
│   ├── mq_auth_records.yml
│   └── mq_chlauth.yml
│
├── .gitignore
│
└── README.md
Running the Automation
Syntax check

Before running the playbook:

ansible-playbook -i inventory/hosts playbooks/configure_mq.yml --syntax-check

ansible-playbook -i inventory/hosts playbooks/configure_mq.yml
If SSH password authentication is being used.
ansible-playbook -i inventory/hosts playbooks/configure_mq.yml --ask-pass --ask-become-pass
Current Status
Implemented

YAML-based MQ object definitions

Modular YAML configuration files

Queue existence checking

Local queue creation

Transmission queue creation

Remote queue creation

Alias queue creation

Channel existence checking

SDR channel creation

RCVR channel creation

Listener existence checking

Listener creation

Topic existence checking

Topic creation

Subscription existence checking

Subscription creation

MQ authority record configuration

CHLAUTH record existence checking

BLOCKUSER CHLAUTH creation

SSLPEERMAP CHLAUTH creation

CHLAUTH idempotency

CHLAUTH security refresh

host_qmgr based object targeting

Create-if-missing logic

Protection against modifying existing MQ objects

Future Enhancements

Ansible roles

CI/CD integration

Jenkins pipeline

Automated testing

Additional MQ object types when required

Additional CHLAUTH rule types when required

Author

Ankur Lodhi

IBM MQ | Solace | MFT | Ansible Automation

This project demonstrates the application of Infrastructure as Code and automation principles to IBM MQ administration.