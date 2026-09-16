from django.contrib import admin
from django.contrib import messages
from .models import Tournament, TournamentRegistration, Round
from .services import create_next_round, complete_round


class RoundInline(admin.TabularInline):
    model = Round
    extra = 0
    readonly_fields = ['number', 'is_complete', 'started_at', 'completed_at']
    can_delete = False


class RegistrationInline(admin.TabularInline):
    model = TournamentRegistration
    extra = 0
    readonly_fields = ['registered_at']


def action_generate_next_round(modeladmin, request, queryset):
    for tournament in queryset:
        try:
            round_obj, pairings, bye_player, errors, bye_players = create_next_round(tournament)
            msg = (
                f'{tournament.name}: Round {round_obj.number} generated, '
                f'{len(pairings)} match(es).'
            )
            # One bye per section, so there can be several.
            if bye_players:
                msg += f' Bye: {", ".join(str(b) for b in bye_players)}.'
            if errors:
                msg += f' Warnings: {"; ".join(errors)}'
            messages.success(request, msg)
        except ValueError as e:
            messages.error(request, f'{tournament.name}: {e}')

action_generate_next_round.short_description = 'Generate next round (Swiss pairing)'


def action_complete_round(modeladmin, request, queryset):
    for tournament in queryset:
        last = tournament.rounds.order_by('-number').first()
        if not last:
            messages.error(request, f'{tournament.name}: No rounds to complete.')
            continue
        try:
            complete_round(last)
            messages.success(request, f'{tournament.name}: Round {last.number} marked complete.')
        except ValueError as e:
            messages.error(request, f'{tournament.name}: {e}')

action_complete_round.short_description = 'Complete current round'


@admin.register(Tournament)
class TournamentAdmin(admin.ModelAdmin):
    list_display = ['name', 'association', 'format', 'status', 'start_date', 'end_date', 'player_count', 'max_players']
    list_filter = ['status', 'format', 'association']
    search_fields = ['name', 'location']
    list_editable = ['status']
    readonly_fields = ['created_at']
    inlines = [RegistrationInline, RoundInline]
    actions = [action_generate_next_round, action_complete_round]

    def player_count(self, obj):
        return obj.player_count
    player_count.short_description = 'Players'


@admin.register(TournamentRegistration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = ['player', 'tournament', 'status', 'registered_at']
    list_filter = ['status', 'tournament']
    list_editable = ['status']
