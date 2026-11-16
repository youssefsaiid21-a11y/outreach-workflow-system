import json
import sys
import uuid
import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import print as rprint
from dotenv import load_dotenv
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError

load_dotenv()

console = Console()


@click.group()
def cli():
    """Outreach CLI  -  Apollo + PhantomBuster + Claude-powered LinkedIn outreach."""
    pass


# ---------------------------------------------------------------------------
# outreach prospect
# ---------------------------------------------------------------------------

@cli.group()
def prospect():
    """Search and manage prospects from Apollo.io."""
    pass


@prospect.command("search")
@click.option("--title", multiple=True, help="Job title filter (can be repeated)")
@click.option("--seniority", multiple=True, help="Seniority filter (e.g. vp, director, c_suite)")
@click.option("--industry", multiple=True, help="Industry filter")
@click.option("--size", multiple=True, help="Employee range (e.g. 51,200)")
@click.option("--location", multiple=True, help="Location filter")
@click.option("--limit", default=50, show_default=True, help="Max prospects to fetch")
@click.option("--segment", default=None, help="Save search as an audience segment with this name")
@click.option("--enrich", is_flag=True, default=False, help="Enrich results with Hunter/Proxycurl/website data")
def search_prospects(title, seniority, industry, size, location, limit, segment, enrich):
    """Search Apollo.io for prospects matching the given filters."""
    from ..services.apollo import ApolloClient
    from ..services.enrichment import EnrichmentClient
    from ..db.database import SessionLocal
    from ..db.models import Prospect, AudienceSegment, ProspectStatus
    import uuid

    apollo = ApolloClient()
    db = SessionLocal()

    try:
        with console.status("[bold cyan]Searching Apollo.io...[/bold cyan]"):
            try:
                raw = apollo.search_all_pages(
                    titles=list(title) or None,
                    seniorities=list(seniority) or None,
                    industries=list(industry) or None,
                    employee_ranges=list(size) or None,
                    locations=list(location) or None,
                    limit=limit,
                    db=db,
                )
            finally:
                apollo.close()

        # Optional enrichment before saving
        if enrich and raw:
            enricher = EnrichmentClient()
            with console.status(f"[bold cyan]Enriching {len(raw)} prospects...[/bold cyan]"):
                raw = enricher.enrich_batch(raw)
            enricher.close()

        # Deduplicate
        existing = {r[0] for r in db.query(Prospect.email).all()}
        new = [p for p in raw if p["email"] not in existing]

        # Resolve segment first so we can tag records at creation time
        seg_id = None
        if segment:
            existing_seg = db.query(AudienceSegment).filter_by(name=segment).first()
            if existing_seg:
                seg_id = existing_seg.id
            else:
                new_seg = AudienceSegment(
                    id=str(uuid.uuid4()),
                    name=segment,
                    filters={
                        "titles": list(title),
                        "seniorities": list(seniority),
                        "industries": list(industry),
                        "employee_ranges": list(size),
                        "locations": list(location),
                    },
                )
                db.add(new_seg)
                db.flush()  # get the segment into the session before tagging prospects
                seg_id = new_seg.id

        saved_ids = []
        skipped_dupes = 0
        for p in new:
            record = Prospect(
                id=str(uuid.uuid4()),
                apollo_id=p.get("apollo_id"),
                first_name=p["first_name"],
                last_name=p.get("last_name", ""),
                email=p["email"],
                title=p.get("title", ""),
                company=p.get("company", ""),
                industry=p.get("industry", ""),
                headcount=p.get("headcount"),
                location=p.get("location", ""),
                linkedin_url=p.get("linkedin_url", ""),
                company_website=p.get("company_website", ""),
                linkedin_headline=p.get("linkedin_headline"),
                linkedin_summary=p.get("linkedin_summary"),
                company_description=p.get("company_description"),
                enriched_at=datetime.now(timezone.utc) if enrich else None,
                segment_id=seg_id,  # tag at creation  -  no secondary update needed
            )
            db.add(record)
            try:
                db.flush()
                saved_ids.append(record.id)
            except IntegrityError:
                db.rollback()
                skipped_dupes += 1

        db.commit()
        if skipped_dupes:
            console.print(f"[yellow]Skipped {skipped_dupes} duplicate(s).[/yellow]")
    finally:
        db.close()

    table = Table(title=f"Search Results ({len(raw)} found, {len(new)} new)")
    table.add_column("Name", style="cyan")
    table.add_column("Title")
    table.add_column("Company")
    table.add_column("Email", style="green")
    table.add_column("Location")

    for p in new[:20]:
        table.add_row(
            f"{p['first_name']} {p.get('last_name', '')}",
            p.get("title", ""),
            p.get("company", ""),
            p["email"],
            p.get("location", ""),
        )
    if len(new) > 20:
        table.add_row(f"... and {len(new) - 20} more", "", "", "", "")

    console.print(table)
    if segment:
        console.print(f"\n[bold green]Saved as segment:[/bold green] {segment}")


@prospect.command("enrich")
@click.option("--segment", default=None, help="Enrich only prospects in this segment")
@click.option("--id", "prospect_ids", multiple=True, help="Enrich specific prospect IDs only")
@click.option("--re-enrich", is_flag=True, default=False, help="Re-run enrichment even if already enriched")
def enrich_prospects(segment, prospect_ids, re_enrich):
    """Enrich saved prospects using Hunter.io, Proxycurl, and website scraping."""
    from ..db.database import SessionLocal
    from ..db.models import Prospect, AudienceSegment
    from ..services.enrichment import EnrichmentClient

    db = SessionLocal()
    enricher = EnrichmentClient()

    try:
        q = db.query(Prospect)
        if prospect_ids:
            q = q.filter(Prospect.id.in_(list(prospect_ids)))
        elif segment:
            seg = db.query(AudienceSegment).filter_by(name=segment).first()
            if not seg:
                console.print(f"[red]Segment '{segment}' not found.[/red]")
                return
            q = q.filter(Prospect.segment_id == seg.id)

        if not re_enrich:
            q = q.filter(Prospect.enriched_at.is_(None))

        prospects = q.all()

        if not prospects:
            console.print("[yellow]No prospects to enrich (all may already be enriched  -  use --re-enrich to force).[/yellow]")
            return

        enriched_count = 0
        with console.status(f"[bold cyan]Enriching {len(prospects)} prospects...[/bold cyan]"):
            for p in prospects:
                prospect_dict = {
                    "first_name": p.first_name,
                    "last_name": p.last_name or "",
                    "email": p.email,
                    "linkedin_url": p.linkedin_url or "",
                    "company_website": p.company_website or "",
                    "company": p.company or "",
                    "linkedin_headline": p.linkedin_headline,
                    "linkedin_summary": p.linkedin_summary,
                    "company_description": p.company_description,
                }
                try:
                    result = enricher.enrich(prospect_dict)
                    updated = False
                    for field in ("linkedin_url", "linkedin_headline", "linkedin_summary", "company_description", "email"):
                        val = result.get(field)
                        if val and not getattr(p, field, None):
                            setattr(p, field, val)
                            updated = True
                    if updated:
                        p.enriched_at = datetime.now(timezone.utc)
                        enriched_count += 1
                except Exception as e:
                    console.print(f"[red]Error enriching {p.email}: {e}[/red]")

        enricher.close()
        db.commit()
    finally:
        db.close()

    console.print(f"\n[bold green]Enrichment complete.[/bold green] Updated {enriched_count} of {len(prospects)} prospects.")


@prospect.command("validate")
@click.option("--all", "validate_all", is_flag=True, default=False, help="Re-validate already-checked emails too")
@click.option("--id", "prospect_ids", multiple=True, help="Validate specific prospect IDs only")
def validate_prospects(validate_all, prospect_ids):
    """Validate prospect emails via Reacher (self-hosted). Skips already-validated by default."""
    from ..db.database import SessionLocal
    from ..db.models import Prospect
    from ..services.reacher import ReacherClient

    db = SessionLocal()

    try:
        try:
            rc = ReacherClient()
        except Exception as e:
            console.print(f"[red]{e}[/red]")
            sys.exit(1)

        console.print("[dim]Reacher: self-hosted (unlimited validations)[/dim]")

        q = db.query(Prospect)
        if prospect_ids:
            q = q.filter(Prospect.id.in_(list(prospect_ids)))
        elif not validate_all:
            q = q.filter(Prospect.email_validation_status.is_(None))

        prospects = q.all()

        if not prospects:
            console.print("[yellow]No prospects to validate. All may already be validated.[/yellow]")
            return

        emails = [p.email for p in prospects]

        with console.status(f"[bold cyan]Validating {len(emails)} emails via Reacher...[/bold cyan]"):
            try:
                results = rc.validate_batch(emails)
            finally:
                rc.close()

        result_map = {r["email"].lower(): r for r in results}
        stats = {}

        for p in prospects:
            result = result_map.get(p.email.lower())
            if not result:
                continue
            p.email_validation_status = result["status"]
            p.email_validation_sub_status = result.get("sub_status", "")
            p.email_validated_at = datetime.now(timezone.utc)
            stats[result["status"]] = stats.get(result["status"], 0) + 1

        db.commit()
    finally:
        db.close()

    table = Table(title=f"Reacher Results  -  {len(prospects)} emails checked")
    table.add_column("Status", style="cyan")
    table.add_column("Count")
    for status, count in sorted(stats.items()):
        color = "green" if status == "valid" else "red" if status in ("invalid", "spamtrap", "abuse", "do_not_mail") else "yellow"
        table.add_row(f"[{color}]{status}[/{color}]", str(count))

    console.print(table)


# ---------------------------------------------------------------------------
# outreach campaign
# ---------------------------------------------------------------------------

@cli.group()
def campaign():
    """Create and manage outreach campaigns."""
    pass


@campaign.command("create")
@click.option("--name", required=True, help="Campaign name")
@click.option("--segment", default=None, help="Audience segment name to target")
@click.option("--goal", required=True, help="Campaign goal (e.g. 'book discovery call')")
@click.option("--value-prop", required=True, help="Your value proposition (2-3 sentences)")
@click.option("--li-note", default="", help="LinkedIn connection note (max 300 chars)")
@click.option("--li-message", default="", help="LinkedIn follow-up message")
def create_campaign(name, segment, goal, value_prop, li_note, li_message):
    """Create a new LinkedIn-only campaign."""
    from ..db.database import SessionLocal
    from ..db.models import Campaign, AudienceSegment, Prospect, EmailSequence, ProspectStatus, SequenceStatus

    db = SessionLocal()

    try:
        segment_id = None
        if segment:
            seg = db.query(AudienceSegment).filter_by(name=segment).first()
            if not seg:
                console.print(f"[red]Segment '{segment}' not found. Run 'outreach prospect search --segment {segment}' first.[/red]")
                sys.exit(1)
            segment_id = seg.id

        camp = Campaign(
            id=str(uuid.uuid4()),
            name=name,
            goal=goal,
            value_prop=value_prop,
            segment_id=segment_id,
        )
        db.add(camp)
        db.commit()

        # Fetch prospects — scope to segment if one is set
        q = db.query(Prospect).filter(Prospect.status == ProspectStatus.pending)
        if segment_id:
            q = q.filter(Prospect.segment_id == segment_id)
        prospects = q.limit(200).all()
        if not prospects:
            console.print("[yellow]No pending prospects found. Search for prospects first.[/yellow]")
            return

        # Create LinkedIn-only sequences for each prospect
        for p in prospects:
            db.add(EmailSequence(
                id=str(uuid.uuid4()),
                campaign_id=camp.id,
                prospect_id=p.id,
                linkedin_connect_note=li_note or None,
                linkedin_message=li_message or None,
                approved=True,
                status=SequenceStatus.pending,
            ))

        db.commit()
        camp_id = camp.id
    finally:
        db.close()

    console.print(Panel(
        f"[bold green]Campaign created![/bold green]\n"
        f"ID: {camp_id}\n"
        f"Prospects: {len(prospects)}\n"
        f"LinkedIn connect note: {'set' if li_note else 'blank'}\n"
        f"LinkedIn message: {'set' if li_message else 'blank'}\n\n"
        f"Next: [cyan]outreach campaign preview --campaign-id {camp_id}[/cyan]",
        title="Campaign Created (LinkedIn-only)"
    ))


@campaign.command("preview")
@click.option("--campaign-id", required=True, help="Campaign ID")
@click.option("--limit", default=3, show_default=True, help="Number of sequences to preview")
def preview_campaign(campaign_id, limit):
    """Preview generated email sequences before sending."""
    from ..db.database import SessionLocal
    from ..db.models import EmailSequence, Prospect

    db = SessionLocal()
    try:
        sequences = db.query(EmailSequence).filter_by(campaign_id=campaign_id).limit(limit).all()

        if not sequences:
            console.print("[red]No sequences found. Run 'outreach campaign create' first.[/red]")
            return

        for seq in sequences:
            prospect = db.query(Prospect).filter_by(id=seq.prospect_id).first()
            name = f"{prospect.first_name} {prospect.last_name or ''}".strip() if prospect else "Unknown"

            console.print(Panel(
                f"[bold]To:[/bold] {name} ({prospect.email if prospect else ''})\n"
                f"[bold]Company:[/bold] {prospect.company if prospect else ''}\n\n"
                f"[bold cyan]Email 1[/bold cyan]  -  {seq.email1_subject}\n{seq.email1_body}\n\n"
                f"[bold cyan]Email 2[/bold cyan]  -  {seq.email2_subject}\n{seq.email2_body}\n\n"
                f"[bold cyan]Email 3[/bold cyan]  -  {seq.email3_subject}\n{seq.email3_body}",
                title=f"Sequence Preview ({seq.id[:8]}...)",
            ))

        console.print(f"\n[dim]Showing {len(sequences)} of {db.query(EmailSequence).filter_by(campaign_id=campaign_id).count()} sequences[/dim]")
        console.print(f"\nLaunch: [cyan]outreach campaign launch --campaign-id {campaign_id}[/cyan]")
    finally:
        db.close()


@campaign.command("launch")
@click.option("--campaign-id", required=True, help="Campaign ID")
@click.confirmation_option(prompt="This will enroll leads into LinkedIn outreach. Continue?")
def launch_campaign(campaign_id):
    """Launch the campaign  -  enroll all approved sequences into LinkedIn outreach."""
    from ..db.database import SessionLocal
    from ..db.models import Campaign, EmailSequence, Prospect, ProspectStatus, CampaignStatus, SequenceStatus

    db = SessionLocal()
    try:
        campaign = db.query(Campaign).filter_by(id=campaign_id).first()
        if not campaign:
            console.print("[red]Campaign not found.[/red]")
            sys.exit(1)

        sequences = db.query(EmailSequence).filter_by(campaign_id=campaign_id).all()
        for seq in sequences:
            seq.approved = True
        db.commit()

        enrolled_count = 0
        with console.status("[bold cyan]Enrolling leads into LinkedIn outreach...[/bold cyan]"):
            for seq in sequences:
                prospect = db.query(Prospect).filter_by(id=seq.prospect_id).first()
                if not prospect:
                    continue
                seq.status = SequenceStatus.enrolled
                prospect.status = ProspectStatus.enrolled
                enrolled_count += 1

            campaign.status = CampaignStatus.active
            campaign.launched_at = datetime.now(timezone.utc)
            db.commit()

        console.print(Panel(
            f"[bold green]Campaign launched![/bold green]\n"
            f"Leads enrolled: {enrolled_count}",
            title="Launch Complete"
        ))
    finally:
        db.close()


@campaign.command("stats")
@click.option("--campaign-id", required=True, help="Campaign ID")
def campaign_stats(campaign_id):
    """Show campaign performance stats."""
    from ..db.database import SessionLocal
    from ..db.models import Campaign, EmailSequence

    db = SessionLocal()
    try:
        campaign = db.query(Campaign).filter_by(id=campaign_id).first()
        if not campaign:
            console.print("[red]Campaign not found.[/red]")
            sys.exit(1)

        sequences = db.query(EmailSequence).filter_by(campaign_id=campaign_id).all()
        status_counts = {}
        for seq in sequences:
            s = seq.status.value if seq.status else "pending"
            status_counts[s] = status_counts.get(s, 0) + 1

        table = Table(title=f"Campaign: {campaign.name}")
        table.add_column("Status", style="cyan")
        table.add_column("Count", style="white")
        for status, count in status_counts.items():
            table.add_row(status, str(count))

        console.print(table)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# outreach style
# ---------------------------------------------------------------------------

@cli.group()
def style():
    """Manage your writing style profile from Outlook."""
    pass


@style.command("sync")
@click.option("--count", default=200, show_default=True, help="Number of sent emails to analyze")
def sync_style(count):
    """Sync writing style from your Outlook sent emails."""
    from ..db.database import SessionLocal
    from ..db.models import StyleProfile
    from ..services.outlook import OutlookClient
    from ..services.style_analyzer import analyze_style
    import uuid

    console.print("[cyan]Opening browser for Outlook authentication...[/cyan]")
    # P3-3: CLI has no HTTP user context; resolve to the first admin user
    # so the per-user token cache file is deterministic. Fall back to the
    # legacy root token file if no users have been seeded yet.
    from ..db.models import User as _User
    _db = SessionLocal()
    try:
        _admin = _db.query(_User).filter_by(role="admin").order_by(_User.created_at.asc()).first()
    finally:
        _db.close()
    if _admin:
        outlook = OutlookClient(user_id=_admin.id)
    else:
        from ..services.outlook import LEGACY_TOKEN_CACHE as _legacy
        outlook = OutlookClient(token_file=_legacy)
    outlook.authenticate()

    with console.status(f"[bold cyan]Fetching {count} sent emails...[/bold cyan]"):
        emails = outlook.fetch_sent_emails(count=count)
        outlook.close()

    if not emails:
        console.print("[red]No sent emails found.[/red]")
        sys.exit(1)

    with console.status("[bold cyan]Analyzing writing style...[/bold cyan]"):
        profile_data = analyze_style(emails)

    db = SessionLocal()
    try:
        profile = db.query(StyleProfile).order_by(StyleProfile.synced_at.desc()).first()
        if profile is None:
            profile = StyleProfile(id=str(uuid.uuid4()))
            db.add(profile)
        profile.avg_word_count = profile_data.get("avg_word_count")
        profile.formality = profile_data.get("formality")
        profile.greeting_patterns = profile_data.get("greeting_patterns", [])
        profile.sign_off_patterns = profile_data.get("sign_off_patterns", [])
        profile.avg_sentence_length = profile_data.get("avg_sentence_length")
        profile.uses_bullet_points = profile_data.get("uses_bullet_points", False)
        profile.bullet_point_frequency = profile_data.get("bullet_point_frequency", 0.0)
        profile.sample_emails = profile_data.get("sample_emails", [])
        profile.raw_profile = profile_data
        db.commit()
    finally:
        db.close()

    console.print(Panel(
        f"[bold green]Style profile synced![/bold green]\n"
        f"Emails analyzed: {profile_data.get('total_emails_analyzed', 0)}\n"
        f"Avg word count: {profile_data.get('avg_word_count', 0)}\n"
        f"Tone: {profile_data.get('formality', 'unknown')}\n"
        f"Greetings: {', '.join(profile_data.get('greeting_patterns', [])[:3])}\n"
        f"Sign-offs: {', '.join(profile_data.get('sign_off_patterns', [])[:3])}",
        title="Style Sync Complete"
    ))


# ---------------------------------------------------------------------------
# outreach inbox
# ---------------------------------------------------------------------------

@cli.group()
def inbox():
    """Manage sending inboxes (legacy — Instantly removed)."""
    pass


@inbox.command("list")
def list_inboxes():
    """List all connected sending inboxes (Outlook)."""
    console.print("[dim]Inbox listing is now available via the dashboard Senders tab.[/dim]")
    console.print("[dim]Outlook inboxes are managed per-user through the web UI.[/dim]")
