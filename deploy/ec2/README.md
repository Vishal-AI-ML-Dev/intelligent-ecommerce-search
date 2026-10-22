# M26A single-EC2 staging demo: runbook

Public URL: <https://intelligent-ecommerce-search.duckdns.org/> (synthetic demo catalog).
This is a staging demo on AWS Free-plan credits. It is **not** a permanent or guaranteed URL.
Owner rule: zero out-of-pocket payment. Never upgrade the Free plan or accept a paid subscription.

| Item | Value |
|---|---|
| Region | ap-southeast-2 (Sydney): the project Region, the only one allowed |
| Instance | `m26a-search`, t3.small (2 GB), Ubuntu 24.04, standard CPU credits, IMDSv2, termination protection |
| Disk | 20 GB gp3, encrypted, deleted with the instance |
| Address | Elastic IP 15.134.113.75. DuckDNS `intelligent-ecommerce-search` points to it |
| Firewall | Security group `m26a-search-ec2` and `ufw`: 80/443 public, 22 only from the owner's IP. Postgres and the app are not published |
| Stack | `/opt/m26a`: `compose.yaml` (db, app, caddy, one-off setup), `Caddyfile`, `.env` (root-only, mode 600, generated on the server) |
| Backups | Daily at 03:30 UTC (`/etc/cron.d/m26a-backup`) into `/opt/m26a/backups`, 7 kept |
| Tags | Every AWS resource has `Project=m26a-search-staging` |

Shell variables used below, in Git Bash on the owner's PC:

```bash
AWS="$LOCALAPPDATA/Programs/AWSCLIV2-extract/Amazon/AWSCLIV2/aws.exe"   # aws login --profile m26a first
P="--profile m26a --region ap-southeast-2"
SSH="ssh -i ~/.ssh/m26a-staging -o UserKnownHostsFile=~/.ssh/known_hosts_m26a ubuntu@15.134.113.75"
ID=i-033ad34a5e29776e8
```

## Everyday commands

- **Status:** `$SSH 'cd /opt/m26a && sudo docker compose ps'`
- **Logs** (they never contain query text): `$SSH 'cd /opt/m26a && sudo docker compose logs --tail 50 app'`
- **Stop the whole server** (stops instance-hour charges; disk and Elastic IP are still charged,
  about $5.60/month): `"$AWS" ec2 stop-instances $P --instance-ids $ID`
- **Start it again:** `"$AWS" ec2 start-instances $P --instance-ids $ID`. Containers restart
  on their own. The first search after a start takes about 13 s while the models load.
- **SSH from a new home IP:** in the console, edit security group `m26a-search-ec2`, rule
  `owner-ssh`, and set it to your current IP with `/32`.

## Update the app (new image)

1. On the PC: `docker save <new-tag> | gzip -1 > new.tar.gz`, then
   `scp -i ~/.ssh/m26a-staging -o UserKnownHostsFile=~/.ssh/known_hosts_m26a new.tar.gz ubuntu@15.134.113.75:/tmp/`
2. `$SSH 'cd /opt/m26a && sudo ./update.sh /tmp/new.tar.gz <new-tag>'`. It takes a backup first.
3. Roll back by running `update.sh` again with the previous tag (and its archive), or by setting
   `APP_IMAGE` back in `.env` and running `sudo docker compose up -d app`.

## Back up, export, restore

- **Back up now:** `$SSH 'cd /opt/m26a && sudo ./backup.sh'`
- **Export the newest backup to the PC:**
  `$SSH 'cd /opt/m26a && sudo sh -c "cat \$(ls -1t backups/*.dump | head -1)"' > ~/m26a-backups/$(date -u +%Y%m%d).dump`
- **Restore** (replaces the database contents):
  `$SSH 'cd /opt/m26a && sudo ./restore.sh backups/<file>.dump'`.
  To restore a dump from the PC, `scp` it into `/opt/m26a/backups/` first.
- The catalog is synthetic and can also be rebuilt from the image. On an empty database, run
  `sudo ./setup-db.sh`.

## Cleanup before credits run out (conservative target: by 2027-01-15)

At about $25–26/month (estimate) the $100 credits last until about mid-February 2027. AWS closes
a Free-plan account when its credits run out. Clean up **by 2027-01-15**, or earlier if the
credit balance in the Billing console falls below $25.

1. Export a final backup (see above) and check that the file is not empty.
2. **Deleting the server deletes its database. Get the owner's explicit confirmation first.**
3. `"$AWS" ec2 modify-instance-attribute $P --instance-id $ID --no-disable-api-termination`
4. `"$AWS" ec2 terminate-instances $P --instance-ids $ID`, then
   `"$AWS" ec2 wait instance-terminated $P --instance-ids $ID`
5. Release the Elastic IP (it is charged even when unattached):
   `"$AWS" ec2 release-address $P --allocation-id eipalloc-03cfcf0811e5e7356`
6. `"$AWS" ec2 delete-security-group $P --group-id sg-0b088b3b928b76b77` and
   `"$AWS" ec2 delete-key-pair $P --key-name m26a-staging`
7. Check that nothing remains:
   `"$AWS" resourcegroupstaggingapi get-resources $P --tag-filters Key=Project,Values=m26a-search-staging`
8. In DuckDNS, delete the subdomains (or point them elsewhere).
