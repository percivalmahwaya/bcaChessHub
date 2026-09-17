from django.test import TestCase, Client
from django.urls import reverse
from django.core import mail
from .models import Association


def make_assoc():
    return Association.objects.create(
        name='Bulawayo Chess Association',
        city='Bulawayo',
        country='Zimbabwe',
        email='bca@chess.zw',
        phone='+263 77 000 0000',
    )


class AssociationListViewTest(TestCase):
    def test_list_shows_active_associations(self):
        make_assoc()
        Association.objects.create(
            name='Inactive Club', city='Harare', email='inactive@chess.zw', is_active=False
        )
        response = self.client.get(reverse('club_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Bulawayo Chess Association')
        self.assertNotContains(response, 'Inactive Club')


class AssociationDetailViewTest(TestCase):
    def setUp(self):
        self.assoc = make_assoc()

    def test_detail_page_loads(self):
        response = self.client.get(reverse('club_detail', args=[self.assoc.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Bulawayo Chess Association')

    def test_detail_page_links_to_the_contact_form(self):
        """There must be a route to the contact page from a club's page.

        This used to assert the literal string 'Contact Us'. That is the
        button's WORDING, not the requirement, so rewording it to "Contact
        this club" during the 2026-09-16 redesign failed a test that was not
        actually about anything that had broken. A test tied to copy teaches
        people to edit tests until they pass, which is how a suite stops
        meaning anything. Assert the link.
        """
        response = self.client.get(reverse('club_detail', args=[self.assoc.pk]))
        self.assertContains(
            response, reverse('club_contact', args=[self.assoc.pk]))


class ContactFormTest(TestCase):
    def setUp(self):
        self.assoc = make_assoc()
        self.url = reverse('club_contact', args=[self.assoc.pk])
        self.client = Client()

    def test_contact_page_loads(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Bulawayo Chess Association')

    def test_valid_submission_sends_email(self):
        response = self.client.post(self.url, {
            'name': 'John Smith',
            'email': 'john@example.com',
            'subject': 'Membership query',
            'body': 'I would like to join the club.',
        })
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('bca@chess.zw', mail.outbox[0].recipients())
        self.assertIn('Membership query', mail.outbox[0].subject)
        self.assertIn('John Smith', mail.outbox[0].body)

    def test_valid_submission_redirects_to_detail(self):
        response = self.client.post(self.url, {
            'name': 'John Smith',
            'email': 'john@example.com',
            'subject': 'Membership query',
            'body': 'I would like to join the club.',
        })
        self.assertRedirects(response, reverse('club_detail', args=[self.assoc.pk]))

    def test_missing_field_does_not_send_email(self):
        self.client.post(self.url, {
            'name': 'John Smith',
            'email': 'john@example.com',
            'subject': '',        # missing
            'body': 'Hello.',
        })
        self.assertEqual(len(mail.outbox), 0)

    def test_missing_field_shows_error(self):
        response = self.client.post(self.url, {
            'name': '',
            'email': 'john@example.com',
            'subject': 'Hello',
            'body': 'Message here.',
        })
        self.assertContains(response, 'Please fill in all fields')

    def test_inactive_association_returns_404(self):
        inactive = Association.objects.create(
            name='Gone Club', city='Mutare', email='gone@chess.zw', is_active=False
        )
        response = self.client.get(reverse('club_contact', args=[inactive.pk]))
        self.assertEqual(response.status_code, 404)


class ClubsUrlTest(TestCase):
    """The rename from associations to clubs, 2026-09-17.

    The model is still called Association internally and that is deliberate:
    five apps hold foreign keys to it, and a RenameModel migration across a
    live schema buys nothing a visitor can see. What a visitor sees is a club.
    """

    def setUp(self):
        self.club = Association.objects.create(
            name='Bulawayo Chess Club', city='Bulawayo', email='bcc@test.zw')

    def test_the_list_lives_at_clubs(self):
        self.assertEqual(self.client.get('/clubs/').status_code, 200)

    def test_the_old_associations_path_redirects_rather_than_404s(self):
        """It has been live and linked from the navigation since June, so
        somebody's bookmark is not a good reason to lose them."""
        response = self.client.get('/associations/')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/clubs/')

    def test_an_old_club_link_redirects_to_the_same_club(self):
        response = self.client.get('/associations/%s/' % self.club.pk)
        self.assertEqual(response.status_code, 301)
        self.assertIn('/clubs/%s/' % self.club.pk, response['Location'])

    def test_the_navigation_points_at_clubs(self):
        body = self.client.get('/clubs/').content.decode()
        self.assertIn('href="/clubs/"', body)
        self.assertNotIn('href="/associations/"', body)

    def test_the_word_association_is_gone_from_what_a_visitor_reads(self):
        """Not from the code, which still says Association, but from the
        page. The two are allowed to differ; the page is what matters."""
        body = self.client.get('/clubs/').content.decode()
        self.assertNotIn('association', body.lower().replace('associations_', ''))
