import csv
from io import BytesIO

from django.contrib import messages
from django.db.models import Avg, Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import admin_required, moderator_required
from accounts.models import User
from claims.models import Claim
from items.models import Category, Item, Location
from matching.models import Match, ScoreSnapshot
from matching.features import manifest
from notifications.utils import notify_user


# ---------------------------------------------------------------------------
# Moderator — review queue (synopsis Section 1.2, Moderator capabilities)
# ---------------------------------------------------------------------------
@moderator_required
def moderation(request):
    pending_items = (Item.objects.filter(status=Item.Status.PENDING, is_active=True)
                     .select_related('category', 'location', 'reporter'))
    open_claims = (Claim.objects.filter(status__in=[
        Claim.Status.SUBMITTED, Claim.Status.QUESTIONS, Claim.Status.ANSWERED, Claim.Status.DISPUTED, Claim.Status.APPROVED])
        .select_related('item', 'claimant'))
    return render(request, 'dashboard/moderation.html', {
        'pending_items': pending_items,
        'open_claims': open_claims,
        'pending_count': pending_items.count(),
        'claim_count': open_claims.count(),
    })


@moderator_required
def review_report(request, pk, decision):
    item = get_object_or_404(Item, pk=pk)
    if request.method != 'POST':
        return redirect('dashboard:moderation')
    if item.status != Item.Status.PENDING or not item.is_active:
        messages.error(request,'Only active reports awaiting review can be moderated here.')
        return redirect('dashboard:moderation')
    if decision == 'approve':
        item.status = Item.Status.APPROVED
        item.save(update_fields=['status'])
        from matching.services import run_matching
        run_matching(item)
        notify_user(item.reporter, kind='report',
                    title=f'Report approved — “{item.title}”',
                    body='Your report is now visible in search.',
                    url=item.get_absolute_url())
        messages.success(request, f'Approved “{item.title}”.')
    elif decision == 'reject':
        item.status = Item.Status.REJECTED
        item.save(update_fields=['status'])
        notify_user(item.reporter, kind='report',
                    title=f'Report rejected — “{item.title}”',
                    body='A moderator rejected this report.',
                    url=item.get_absolute_url())
        messages.info(request, f'Rejected “{item.title}”.')
    return redirect('dashboard:moderation')


# ---------------------------------------------------------------------------
# Administrator — analytics dashboard (synopsis Section 4.3, 4.11, 7.4)
# ---------------------------------------------------------------------------
@admin_required
def analytics(request):
    items = Item.objects.all()
    current_matches = Match.objects.filter(configuration=manifest())
    total = items.count()

    by_category = (items.values('category__name', 'category__icon')
                   .annotate(n=Count('id')).order_by('-n')[:8])
    by_location = (items.values('location__name')
                   .annotate(n=Count('id')).order_by('-n')[:8])
    by_status = items.values('status').annotate(n=Count('id'))
    tier_dist = current_matches.values('tier').annotate(n=Count('id'))

    cat_max = max([c['n'] for c in by_category], default=1)
    loc_max = max([l['n'] for l in by_location], default=1)

    context = {
        'kpis': {
            'users': User.objects.count(),
            'reports': total,
            'active_reports': items.filter(is_active=True).count(),
            'matches': current_matches.count(),
            'claims': Claim.objects.count(),
            'returned': items.filter(status=Item.Status.RETURNED).count(),
        },
        'by_category': [dict(c, pct=round(c['n'] * 100 / cat_max)) for c in by_category],
        'by_location': [dict(l, pct=round(l['n'] * 100 / loc_max)) for l in by_location],
        'by_status': list(by_status),
        'tier_dist': list(tier_dist),
        'avg_match': current_matches.aggregate(a=Avg('score'))['a'] or 0,
        'lost_total': items.filter(report_type=Item.Type.LOST).count(),
        'found_total': items.filter(report_type=Item.Type.FOUND).count(),
        'snapshots': ScoreSnapshot.objects.all()[:12],
    }
    return render(request, 'dashboard/analytics.html', context)


@admin_required
def manage_users(request):
    if request.method == 'POST':
        uid = request.POST.get('user_id')
        new_role = request.POST.get('role')
        user = get_object_or_404(User, pk=uid)
        if user.pk == request.user.pk or user.is_superuser:
            messages.error(request,'Your own role and superuser roles cannot be changed here.')
            return redirect('dashboard:users')
        if new_role in dict(User.Role.choices):
            user.role = new_role
            user.save(update_fields=['role'])
            messages.success(request,
                             f'{user.display_name()} is now {user.get_role_display()}.')
        return redirect('dashboard:users')
    users = User.objects.all().order_by('role', 'username')
    return render(request, 'dashboard/users.html',
                  {'users': users, 'roles': User.Role.choices})


@admin_required
def taxonomy(request):
    """Lightweight management of categories and locations."""
    if request.method == 'POST':
        kind = request.POST.get('kind')
        name = (request.POST.get('name') or '').strip()
        if name and kind == 'category':
            from django.utils.text import slugify
            Category.objects.get_or_create(
                slug=slugify(name),
                defaults={'name': name, 'icon': request.POST.get('icon') or '📦'})
            messages.success(request, f'Category “{name}” added.')
        elif name and kind == 'location':
            from django.utils.text import slugify
            Location.objects.get_or_create(slug=slugify(name),
                                           defaults={'name': name})
            messages.success(request, f'Location “{name}” added.')
        return redirect('dashboard:taxonomy')
    return render(request, 'dashboard/taxonomy.html', {
        'categories': Category.objects.annotate(n=Count('items')),
        'locations': Location.objects.annotate(n=Count('items')),
    })


# ---------------------------------------------------------------------------
# Exports (synopsis Section 7.4 — CSV export + PDF report)
# ---------------------------------------------------------------------------
@admin_required
def export_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="lostlink_reports.csv"'
    writer = csv.writer(response)
    writer.writerow(['ID', 'Type', 'Title', 'Category', 'Location', 'Event date',
                     'Status', 'Brand', 'Color', 'Reporter', 'Created'])
    for it in Item.objects.select_related('category', 'location', 'reporter'):
        row = [
            it.id, it.get_report_type_display(), it.title, it.category.name,
            it.location.name, it.event_date, it.get_status_display(),
            it.brand, it.color, it.reporter.username,
            it.created_at.strftime('%Y-%m-%d %H:%M')]
        writer.writerow(["'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v for v in row])
    return response


@admin_required
def export_pdf(request):
    """Generate a periodic summary PDF (Section 7.4)."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                        Table, TableStyle)
    except Exception:
        messages.error(request,
                       'PDF export needs reportlab. Run: pip install reportlab')
        return redirect('dashboard:analytics')

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title='LostLink Summary Report')
    styles = getSampleStyleSheet()
    ink = colors.HexColor('#241E2E')
    brand = colors.HexColor('#E8623C')
    story = []

    title = styles['Title']
    title.textColor = brand
    story.append(Paragraph('LostLink — Lost &amp; Found Summary Report', title))
    story.append(Paragraph(
        timezone.now().strftime('Generated %d %B %Y, %H:%M'), styles['Normal']))
    story.append(Spacer(1, 8 * mm))

    items = Item.objects.all()
    month = request.GET.get('month','')
    if month:
        import datetime
        try:
            period = datetime.datetime.strptime(month,'%Y-%m')
        except ValueError:
            return HttpResponse('Use YYYY-MM for month.',status=400)
        items = items.filter(created_at__year=period.year,created_at__month=period.month)
        story.append(Paragraph(f'Report period: {month}',styles['Normal']))
    kpis = [
        ['Metric', 'Value'],
        ['Total reports', items.count()],
        ['Lost reports', items.filter(report_type=Item.Type.LOST).count()],
        ['Found reports', items.filter(report_type=Item.Type.FOUND).count()],
        ['Items returned', items.filter(status=Item.Status.RETURNED).count()],
        ['Potential matches', Match.objects.filter(configuration=manifest()).count()],
        ['Registered users', User.objects.count()],
    ]
    t = Table(kpis, colWidths=[90 * mm, 60 * mm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), ink),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1),
         [colors.HexColor('#FAF6F1'), colors.white]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E4DCD0')),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    story.append(Spacer(1, 8 * mm))

    story.append(Paragraph('Most commonly lost/found categories', styles['Heading2']))
    cat_rows = [['Category', 'Reports']]
    for c in (items.values('category__name').annotate(n=Count('id'))
              .order_by('-n')[:8]):
        cat_rows.append([c['category__name'], c['n']])
    ct = Table(cat_rows, colWidths=[90 * mm, 60 * mm])
    ct.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), brand),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E4DCD0')),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(ct)
    story.append(Spacer(1, 8 * mm))

    story.append(Paragraph('Most problematic locations', styles['Heading2']))
    loc_rows = [['Location', 'Reports']]
    for l in (items.values('location__name').annotate(n=Count('id'))
              .order_by('-n')[:8]):
        loc_rows.append([l['location__name'], l['n']])
    lt = Table(loc_rows, colWidths=[90 * mm, 60 * mm])
    lt.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#12A594')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E4DCD0')),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(lt)

    doc.build(story)
    buf.seek(0)
    resp = HttpResponse(buf, content_type='application/pdf')
    resp['Content-Disposition'] = 'attachment; filename="lostlink_report.pdf"'
    return resp
